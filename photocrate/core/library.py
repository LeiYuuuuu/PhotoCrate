"""照片库：扫描 → 读元数据 → 像素分析 → 元数据分析，全部带缓存。

界面层只和 Library 打交道；耗时步骤都接受 progress 回调与 cancel 事件，在后台线程中调用。
"""
from __future__ import annotations

import concurrent.futures as cf
from concurrent.futures.process import BrokenProcessPool
import logging
import multiprocessing as mp
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..analyzers.base import ANALYZERS, AnalysisContext, meta_analyzers, pixel_analyzers
from ..analyzers.builtin import infer_missing_locations
from ..config import Settings, plugin_dirs, thumbs_dir, user_cache_dir, worker_count
from .cache import FeatureCache
from .dates import compile_patterns
from .geo import GeoService
from .places import AnnotationStore
from .metadata import MetadataBackend, create_backend, exiftool_version, find_exiftool, merge_meta
from .models import PhotoGroup
from .pixelwork import init_worker, process_task, thumb_path_for
from .scanner import scan

log = logging.getLogger(__name__)


@dataclass
class Progress:
    stage: str              # enumerate / meta / pixel / analyze / done
    message: str = ""
    meta_done: int = 0
    meta_total: int = 0
    pix_done: int = 0
    pix_total: int = 0
    groups: int = 0
    current: str = ""

    @property
    def fraction(self) -> float:
        if self.stage == "enumerate":
            return 0.02
        if self.stage == "done":
            return 1.0
        m = self.meta_done / self.meta_total if self.meta_total else 1.0
        p = self.pix_done / self.pix_total if self.pix_total else 1.0
        return 0.05 + 0.25 * m + 0.65 * p


ProgressCb = Callable[[Progress], None]


@dataclass
class ScanReport:
    groups: int = 0
    files: int = 0
    meta_cached: int = 0
    meta_read: int = 0
    pix_cached: int = 0
    pix_computed: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    cancelled: bool = False


class Library:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.groups: list[PhotoGroup] = []
        self.selected: set[int] = set()
        self.roots: list[str] = []
        self.cache = FeatureCache(user_cache_dir() / "cache.db")
        self.annotations = AnnotationStore()
        self.exiftool_path: str | None = None
        self.exiftool_ver: str | None = None
        self.refresh_exiftool()

    # ---- 工具状态 --------------------------------------------------------
    def refresh_exiftool(self) -> None:
        self.exiftool_path = find_exiftool(self.settings.get("exiftool_path", ""))
        self.exiftool_ver = exiftool_version(self.exiftool_path) if self.exiftool_path else None
        if self.exiftool_path and not self.exiftool_ver:
            self.exiftool_path = None

    def by_gid(self, gid: int) -> PhotoGroup | None:
        if 0 <= gid < len(self.groups) and self.groups[gid].gid == gid:
            return self.groups[gid]
        for g in self.groups:
            if g.gid == gid:
                return g
        return None

    # ---- 扫描 ------------------------------------------------------------
    def scan(self, roots: list[str], recursive: bool, progress: ProgressCb | None = None,
             cancel: threading.Event | None = None) -> ScanReport:
        t0 = time.time()
        cancel = cancel or threading.Event()
        rep = ScanReport()
        pg = Progress("enumerate", "正在枚举文件…")

        def emit():
            if progress:
                progress(pg)

        emit()
        last = [0.0]

        def on_enum(n, d):
            pg.groups = n
            pg.current = d
            now = time.time()
            if now - last[0] > 0.15:
                last[0] = now
                emit()

        groups = scan(roots, recursive, bool(self.settings.get("pair_live_photo", True)), cancel, on_enum)
        rep.groups = len(groups)
        rep.files = sum(len(g.files) for g in groups)
        pg.groups = len(groups)
        if cancel.is_set():
            rep.cancelled = True
            return rep

        self._analyze(groups, pg, emit, cancel, rep)
        if cancel.is_set():
            rep.cancelled = True
            return rep
        self.groups = groups
        self.roots = list(roots)
        self.selected = set()
        rep.seconds = time.time() - t0
        pg.stage = "done"
        pg.message = "完成"
        emit()
        return rep

    def _analyze(self, groups: list[PhotoGroup], pg: Progress, emit: Callable[[], None],
                 cancel: threading.Event, rep: ScanReport) -> None:
        # 1) 像素任务（缓存未命中的部分）先提交到进程池，与 ExifTool 读取并行
        prim_sigs = [g.primary_file.sig for g in groups]
        pix_cache = self.cache.get_pix_many(prim_sigs)
        prev_cache = self.cache.get_preview_many(prim_sigs)
        wanted = {a.id: a.version for a in pixel_analyzers()}
        troot = str(thumbs_dir())
        tasks = []
        for g, sig in zip(groups, prim_sigs):
            have = pix_cache.get(sig, {})
            for aid, (ver, data) in have.items():
                if aid in wanted and ver == wanted[aid]:
                    g.feats[aid] = data
            pv = prev_cache.get(sig)
            if pv and pv.get("thumb") and os.path.exists(pv["thumb"]):
                g.thumb = pv["thumb"]
                g.feats["preview"] = {"w": pv["w"], "h": pv["h"], "src": pv["src"]}
            need = [aid for aid, ver in wanted.items() if aid not in g.feats]
            need_thumb = g.thumb is None
            if need or need_thumb:
                tasks.append({
                    "gid": g.gid, "sig": sig, "path": g.primary, "kind": g.primary_file.role,
                    "need": need, "need_thumb": need_thumb,
                    "thumb_path": thumb_path_for(troot, sig), "exiftool": self.exiftool_path,
                })
            else:
                rep.pix_cached += 1
        pg.pix_total = len(tasks)

        pool, futures = self._start_pool(tasks, cancel)

        # 2) 元数据（缓存未命中的部分用 ExifTool 批量读）
        files = [f for g in groups for f in g.image_files]
        sig_of = {f.path: f.sig for f in files}
        cached = self.cache.get_meta_many(list(sig_of.values()))
        todo = [p for p, s in sig_of.items() if s not in cached]
        rep.meta_cached = len(files) - len(todo)
        pg.stage = "meta"
        pg.meta_total = len(todo)
        pg.message = "正在读取元数据…"
        emit()
        results: dict[str, dict[str, Any]] = {}
        if todo:
            backend: MetadataBackend = create_backend(self.exiftool_path)
            lock = threading.Lock()
            last = [0.0]

            def on_meta(n):
                with lock:
                    pg.meta_done += n
                now = time.time()
                if now - last[0] > 0.2:
                    last[0] = now
                    self._drain(futures, groups, pg, rep, block=False)
                    emit()
            try:
                results = backend.read_many(todo, on_meta, cancel)
            finally:
                backend.close()
            new = {sig_of[p]: d for p, d in results.items()}
            if new:
                self.cache.put_meta_many(new)
            rep.meta_read = len(results)
        for g in groups:
            items = []
            # 主图优先，其次其他图片
            for f in sorted(g.image_files, key=lambda f: f.path != g.primary):
                d = results.get(f.path) or cached.get(f.sig)
                if d:
                    items.append(d)
            g.meta = merge_meta(items)
        if cancel.is_set():
            self._stop_pool(pool, futures)
            return

        # 3) 等像素任务全部完成
        pg.stage = "pixel"
        pg.message = "正在分析画面（缩略图 / 色调 / 清晰度）…"
        emit()
        while futures and not cancel.is_set():
            self._drain(futures, groups, pg, rep, block=True)
            emit()
        self._stop_pool(pool, futures)
        self.cache.commit()
        if cancel.is_set():
            return

        # 4) 元数据分析器（日期 / 地区 / 相机 / 画幅 …）
        pg.stage = "analyze"
        pg.message = "正在整理日期与地点…"
        emit()
        self.run_meta_analyzers(groups)

    # ---- 进程池 ----------------------------------------------------------
    def _start_pool(self, tasks: list[dict], cancel: threading.Event):
        if not tasks:
            return None, {}
        n = worker_count(self.settings)
        dirs = [str(d) for d in plugin_dirs()]
        settings = self.settings.snapshot()
        pool: cf.Executor
        mode = self.settings.get("parallel_mode", "process")
        if mode == "process" and n > 1 and len(tasks) > 8:
            try:
                pool = cf.ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn"),
                                              initializer=init_worker, initargs=(dirs, settings, True))
            except Exception as e:
                log.warning("无法启动多进程，改用多线程：%s", e)
                init_worker(dirs, settings, False)
                pool = cf.ThreadPoolExecutor(max_workers=n)
        else:
            init_worker(dirs, settings, False)
            pool = cf.ThreadPoolExecutor(max_workers=max(1, n))
        futures = {}
        for t in tasks:
            futures[pool.submit(process_task, t)] = t
        return pool, futures

    def _drain(self, futures: dict, groups: list[PhotoGroup], pg: Progress, rep: ScanReport, block: bool) -> None:
        if not futures:
            return
        done, _ = cf.wait(list(futures), timeout=0.25 if block else 0, return_when=cf.FIRST_COMPLETED)
        for fut in done:
            task = futures.pop(fut)
            try:
                res = fut.result()
            except BrokenProcessPool as e:
                # 进程池崩溃：在本线程内补算
                log.warning("后台进程异常，改为本线程处理：%s", e)
                init_worker([str(d) for d in plugin_dirs()], self.settings.snapshot(), False)
                res = process_task(task)
            except Exception as e:
                res = {"sig": task["sig"], "gid": task["gid"], "error": str(e), "feats": {}}
            g = groups[task["gid"]]
            if res.get("error"):
                rep.errors.append(f"{g.primary}: {res['error']}")
            for aid, item in (res.get("feats") or {}).items():
                g.feats[aid] = item["data"]
            if res.get("thumb"):
                g.thumb = res["thumb"]
            if res.get("preview"):
                g.feats["preview"] = res["preview"]
            if res.get("feats") or res.get("preview"):
                self.cache.put_pixel_result(res)
            pg.pix_done += 1
            rep.pix_computed += 1
            pg.current = g.name
        if pg.pix_done % 50 == 0:
            self.cache.commit()

    @staticmethod
    def _stop_pool(pool, futures) -> None:
        if pool is None:
            return
        for f in futures:
            f.cancel()
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass

    # ---- 元数据分析器（改设置后可单独重跑，几乎瞬时） ------------------------
    def run_meta_analyzers(self, groups: list[PhotoGroup] | None = None) -> None:
        groups = self.groups if groups is None else groups
        s = self.settings.snapshot()
        ctx = AnalysisContext(
            settings=s,
            geo=GeoService(bool(s.get("gps_assume_gcj02"))),
            date_patterns=compile_patterns(s.get("filename_patterns")),
            extras={"manual_places": self.annotations.get_many([g.primary_file.sig for g in groups if g.files])},
        )
        for a in meta_analyzers():
            for g in groups:
                try:
                    r = a.analyze(g, ctx)
                    if r is not None:
                        g.feats[a.id] = r
                    else:
                        g.feats.pop(a.id, None)
                except Exception as e:
                    log.warning("分析器 %s 处理 %s 失败：%s", a.id, g.primary, e)
        if s.get("geo_infer"):
            infer_missing_locations(groups, int(s.get("geo_infer_minutes", 30)))

    # ---- 文件移动后同步路径 ----------------------------------------------
    def apply_moves(self, mapping: dict[str, str]) -> None:
        """mapping: 旧路径(normcase) → 新路径。"""
        if not mapping:
            return
        for g in self.groups:
            for f in g.files:
                new = mapping.get(os.path.normcase(os.path.abspath(f.path)))
                if new:
                    if f.path == g.primary:
                        g.primary = new
                    f.path = new

    def remove_groups(self, gids: set[int]) -> None:
        """撤销"复制"等操作后，从库中去掉已不存在的文件（保留 gid 编号稳定）。"""
        for g in self.groups:
            if g.gid in gids:
                g.files = [f for f in g.files if os.path.exists(f.path)]

    # ---- 手动指定地点 ----------------------------------------------------
    def set_manual_place(self, gids: list[int], place: dict[str, Any] | None, override_gps: bool = False) -> int:
        """给一批照片指定地点（place=None 表示清除）。返回实际改动的组数。"""
        changed = 0
        to_set: dict[str, dict[str, Any]] = {}
        to_clear: list[str] = []
        for gid in gids:
            g = self.by_gid(gid)
            if g is None or not g.files:
                continue
            sig = g.primary_file.sig
            if place is None:
                to_clear.append(sig)
                changed += 1
                continue
            if g.feat("geo", "has_gps") and not override_gps:
                continue
            to_set[sig] = {**place, "override": bool(override_gps)}
            changed += 1
        if to_set:
            self.annotations.set_many(to_set)
        if to_clear:
            self.annotations.clear_many(to_clear)
        return changed

    def close(self) -> None:
        self.cache.close()
        self.annotations.close()

    # ---- 便捷 ------------------------------------------------------------
    def scope_groups(self, scope: str) -> list[PhotoGroup]:
        if scope == "selected" and self.selected:
            return [g for g in self.groups if g.gid in self.selected and g.files]
        return [g for g in self.groups if g.files]

    @property
    def analyzer_names(self) -> list[str]:
        return [a.label or a.id for a in ANALYZERS.values()]
