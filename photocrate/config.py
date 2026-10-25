"""路径与设置。

- 资源目录：程序自带的 data/、plugins/、tools/ 所在位置（开发时为项目根目录，打包后为 exe 所在目录）。
- 用户数据目录：%APPDATA%\\PhotoCrate（设置、操作日志 journal.db、插件、日志）。
- 缓存目录：%LOCALAPPDATA%\\PhotoCrate（元数据/特征缓存 cache.db、缩略图）。
- 便携模式：exe 旁放一个 portable.flag 文件，所有数据存到 exe 旁的 userdata/ 下。
- 环境变量 PHOTOCRATE_HOME 可覆盖数据位置（测试用）。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_ID = "PhotoCrate"
log = logging.getLogger(__name__)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def app_dir() -> Path:
    """程序所在目录（打包后为 exe 所在目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dirs() -> list[Path]:
    """查找自带资源的候选目录（PyInstaller 会把数据放到 _internal/）。"""
    dirs = [app_dir()]
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        dirs.append(Path(mei))
    return dirs


def find_resource(*parts: str) -> Path | None:
    for d in resource_dirs():
        p = d.joinpath(*parts)
        if p.exists():
            return p
    return None


def _base_dirs() -> tuple[Path, Path]:
    home = os.environ.get("PHOTOCRATE_HOME")
    if home:
        root = Path(home)
        return root / "data", root / "cache"
    if (app_dir() / "portable.flag").exists():
        root = app_dir() / "userdata"
        return root / "data", root / "cache"
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return roaming / APP_ID, local / APP_ID
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return data / APP_ID, cache / APP_ID


def user_data_dir() -> Path:
    d = _base_dirs()[0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_cache_dir() -> Path:
    d = _base_dirs()[1]
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbs_dir() -> Path:
    d = user_cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def screenshots_dir() -> Path:
    """F12 截图保存位置：用户数据目录下的 screenshots/（便携模式时在程序目录 userdata 里）。"""
    d = user_data_dir() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugin_dirs() -> list[Path]:
    """插件目录：程序自带 plugins/ + 用户数据目录下的 plugins/。"""
    user = user_data_dir() / "plugins"
    user.mkdir(parents=True, exist_ok=True)
    dirs = []
    for d in resource_dirs():
        if (d / "plugins").is_dir():
            dirs.append(d / "plugins")
    dirs.append(user)
    # 去重
    seen, out = set(), []
    for d in dirs:
        k = str(d.resolve()).lower()
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "theme": "light",                 # light = 终末地·浅色工业风；dark = 方舟·深色终端风
    "ui_motion": True,               # 可即时关闭的轻量界面动效
    "window_state": {},               # 上次关闭时的窗口位置/大小
    "window_screen": "",              # 下次启动在哪台显示器打开：primary / secondary / #2 / 显示器名称片段
    "exiftool_path": "",              # 留空 = 自动查找
    "recursive": True,
    "source_dirs": [],
    "target_dir": "",
    # 并行
    "parallel_mode": "process",       # process / thread
    "workers": 0,                     # 0 = 自动（CPU 核数 - 1，最多 8）
    # 日期
    "date_use_filename": True,
    "date_use_mtime": True,
    "filename_patterns": None,        # None = 使用内置规则（见 core/dates.py）
    # 地区
    "gps_assume_gcj02": False,
    "geo_infer": False,
    "geo_infer_minutes": 30,
    # GPS 补全（按拍摄时间匹配轨迹 / 参考照片）
    "gps_fill": True,
    "gps_fill_library": True,         # 同批里自带 GPS 的照片（通常是手机）也作为参考
    "gps_ref_dirs": [],               # 参考照片文件夹（只读取，不会移动）
    "gps_track_files": [],            # GPX / KML 轨迹文件
    "gps_fill_minutes": 30,           # 时间相差多少分钟以内算匹配
    "camera_tz_default": "",          # 照片没有时区信息时按哪个时区（如 +09:00），留空 = 未知
    # 写回 XMP 附属文件
    "xmp_write_gps": True,
    "xmp_write_place": True,
    "xmp_write_time": True,
    # 色调阈值
    "color_mono_chroma": 7.0,
    "color_warm_margin": 0.12,
    "color_low_sat_chroma": 9.0,
    "color_dark_l": 35.0,
    "color_bright_l": 65.0,
    # 清晰度
    "blur_threshold": 60.0,
    # 连拍 / 相似分组
    "similar_gap_seconds": 10,        # 相邻两张最多相差几秒
    "similar_threshold": 12,          # 感知哈希最多相差几位（0–64，越小越严格）
    "similar_folder": "待确认_相似",
    "xmp_write_rating": True,
    "xmp_write_keywords": True,       # 写 XMP 时带上 AI 关键词（场景 / 已命名的人物 / 旅行名）
    "include_videos": True,           # 扫描时包含独立视频（.mp4 / .mov / .mts …）
    "import_template": "{年}/{年月日}",  # 导入向导：存储卡照片放到哪个文件夹
    "import_dir": "",
    "export_dir": "",
    "xmp_update_existing": True,      # 已有 .xmp 时更新其中对应字段（先备份，可撤销）
    # 批量重命名
    "rename_template": "{日期}_{市|省|国家|未知地点}_{序号}",
    # 旅行自动分段
    "home_places": [],                # 常住地 [{label, lat, lon, radius_km, names}]
    "trip_gap_hours": 36,             # 相邻两张“离家”照片间隔超过多少小时算两次旅行
    "trip_min_photos": 3,             # 少于几张离家照片的不算旅行
    "trip_tail_hours": 12,            # 没有地点的照片：在旅行开始前 / 结束后多少小时内也算进这次旅行
    "trip_names": {},                 # 用户改过的旅行名 {起始日期: 名称}
    "trip_other_name": "日常",        # 分区时不属于任何旅行的照片放进的文件夹
    # 地图
    "map_tiles": "auto",              # auto（OpenStreetMap，深色主题下压暗）/ osm / amap / none
    # AI（模型首次使用时下载到用户数据目录 models\）
    "ai_enabled": True,               # 模型就绪后，扫描时自动做场景识别 / 文字搜图索引
    "ai_faces": True,                 # 模型就绪后，扫描时自动检测人脸
    "ocr_enabled": True,              # 文字识别模型就绪后，扫描时自动识别照片里的文字
    "photos_zoom": 5,                 # 「相册」网格每行几张（3–12）
    "ai_mirror": "auto",              # 模型下载源：auto（先官方后镜像）/ hf / mirror
    "face_threshold": 0.42,           # 人脸相似度（余弦）达到多少算同一人
    "face_min_size": 40,              # 预览图（长边 1024）里小于多少像素的脸忽略
    "people_other_name": "其他人",
    # 文件
    "pair_live_photo": True,
    "pair_across_folders": True,      # RAW 与 JPG 分处不同文件夹时，按文件名 + 拍摄时间重新配对
    "verify_hash": False,
    "operation": "move",              # move / copy
    "conflict": "rename",             # rename / skip
    "split_raw_jpg": "none",          # none / raw_sub（RAW 放入 RAW 子文件夹）/ both_sub
    # 分区条件（上次使用）
    "partition_mode": "rules",
    "partition_rules": [
        {"dim": "date", "opts": {"granularity": "y/ym"}},
    ],
    "partition_template": "{年}/{年}-{月}",
    "scope": "all",                   # all / selected
}


class Settings:
    """线程安全的 JSON 设置。未知键会原样保留，方便插件存自己的设置。"""

    def __init__(self, path: Path | None = None):
        self.path = path or (user_data_dir() / "settings.json")
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict):
                with self._lock:
                    self._data.update(data)
        except Exception as e:  # 设置文件损坏不影响启动
            log.warning("读取设置失败，使用默认值：%s", e)

    def save(self) -> None:
        with self._lock:
            text = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return copy.deepcopy(self._data[key])
            return copy.deepcopy(DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self._data[key] = value
        if save:
            self.save()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)


def worker_count(settings: Settings) -> int:
    n = int(settings.get("workers") or 0)
    if n <= 0:
        n = max(1, min(8, (os.cpu_count() or 2) - 1))
    return n
