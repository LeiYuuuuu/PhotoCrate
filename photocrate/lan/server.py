"""手机互传的网页服务（标准库 http.server，不碰 Qt）。

- 只在「手机互传」窗口开着时运行；每次启动生成一个随机口令，所有网址都在 /<口令>/ 下面，口令不对一律 404，
  所以同一个 Wi-Fi 里的别人猜不到地址。
- 上传：网页用 PUT 把每个文件的原始字节直接发过来（不用 multipart，大视频也是边收边写），只收照片 / 视频格式，
  文件名去掉路径、非法字符；先写成 .pcupload 临时文件（自动同步不认这个扩展名），收完再改成正式名字，
  重名自动加编号、绝不覆盖；同名同大小的视为已经传过，跳过。修改时间设成手机上的文件时间（没有 EXIF 的截图也能排对日期）。
- 浏览（只读）：照片列表来自界面线程给的快照（隐藏的照片不在里面）；缩略图优先用照片库的缩略图缓存，
  大图按需生成并缓存在内存；视频支持 Range，手机上能拖进度条。
"""
from __future__ import annotations

import io
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from ..core.formats import IMAGE_EXTS, VIDEO_EXTS
from ..ops.pathsafe import sanitize_component

DEFAULT_PORT = 8765
MAX_UPLOAD = 8 * 1024 ** 3          # 单个文件最大 8 GB
CHUNK = 1 << 20
PREVIEW_SIDE = 2048
THUMB_SIDE = 360


@dataclass
class Item:
    gid: int
    name: str
    date: str                        # ISO 时间或 ""
    video: bool
    path: str                        # 主文件（视频 / 图片）
    role: str
    thumb: str | None = None


@dataclass
class Snapshot:
    """界面线程整理好的只读数据：网页上能看到的照片（新的在前）与相簿。"""
    items: list[Item] = field(default_factory=list)
    albums: list[tuple[str, str, list[int]]] = field(default_factory=list)      # (键, 名称, gids)

    def __post_init__(self) -> None:
        self.index = {it.gid: it for it in self.items}

    def by_gid(self) -> dict[int, Item]:
        return self.index


def lan_addresses() -> list[str]:
    """本机的局域网 IPv4 地址，常见的家用网段排前面。"""
    found: list[str] = []
    for probe in ("10.255.255.255", "8.8.8.8"):  # UDP connect 不真的发包：只是让系统选出“上网用的那块网卡”
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 1))
            ip = s.getsockname()[0]
            s.close()
            if ip not in found:
                found.append(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass

    def rank(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31:
            return 2
        return 9
    ips = [ip for ip in found if not ip.startswith(("127.", "169.254."))]
    return sorted(ips, key=rank) or ["127.0.0.1"]


def safe_name(name: str) -> str | None:
    """上传的文件名 → 只留文件名本身、去掉非法字符；不是照片 / 视频返回 None。"""
    base = os.path.basename(str(name).replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
        return None
    stem = sanitize_component(stem)[:80] or "photo"
    return stem + ext


def unique_target(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    n = 0
    while True:
        p = os.path.join(folder, name if n == 0 else f"{stem} ({n}){ext}")
        if not os.path.exists(p):
            return p
        n += 1


class _LRU:
    def __init__(self, cap: int):
        self.cap = cap
        self.d: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def get(self, k):
        with self.lock:
            v = self.d.get(k)
            if v is not None:
                self.d.move_to_end(k)
            return v

    def put(self, k, v) -> None:
        with self.lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.cap:
                self.d.popitem(last=False)


class LanServer:
    def __init__(self, upload_dir: str | None, allow_upload: bool = True, allow_browse: bool = True,
                 port: int = DEFAULT_PORT, exiftool: str | None = None, strings: dict | None = None,
                 host: str = "0.0.0.0"):
        self.token = secrets.token_urlsafe(9)
        self.upload_dir = upload_dir
        self.allow_upload = allow_upload
        self.allow_browse = allow_browse
        self.exiftool = exiftool
        self.strings = strings or {}
        self.snapshot = Snapshot()
        self.on_upload: Callable[[str], None] | None = None     # 收完一个文件（在服务线程里调用）
        self.received: list[str] = []
        self.thumbs = _LRU(600)
        self.previews = _LRU(40)
        self._thread: threading.Thread | None = None
        self.httpd = None
        for p in (port, port + 1, port + 2, 0):              # 端口被占用就换一个
            try:
                self.httpd = ThreadingHTTPServer((host, p), _Handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            raise OSError("no free port")
        self.httpd.daemon_threads = True
        self.httpd.app = self                                # type: ignore[attr-defined]

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def url(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/{self.token}/"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="lan-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    def set_snapshot(self, snap: Snapshot) -> None:
        self.snapshot = snap                                 # 整个替换（读的线程拿到的总是完整的一份）

    # ---- 图片 ----
    def thumb_bytes(self, it: Item) -> bytes | None:
        b = self.thumbs.get(it.gid)
        if b is None:
            if it.thumb and os.path.exists(it.thumb):
                with open(it.thumb, "rb") as f:
                    b = f.read()
            else:
                b = self._render(it, THUMB_SIDE, 78)
            if b:
                self.thumbs.put(it.gid, b)
        return b

    def preview_bytes(self, it: Item) -> bytes | None:
        b = self.previews.get(it.gid)
        if b is None:
            b = self._render(it, PREVIEW_SIDE, 86)
            if b:
                self.previews.put(it.gid, b)
        return b

    def _render(self, it: Item, side: int, quality: int) -> bytes | None:
        from ..core.imaging import load_preview
        try:
            im = load_preview(it.path, "video" if it.video else it.role, side, self.exiftool)[0].convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            return buf.getvalue()
        except Exception:
            return None

    # ---- 上传 ----
    def receive(self, name: str, length: int, read: Callable[[int], bytes], mtime_ms: float | None) -> dict:
        if not (self.allow_upload and self.upload_dir):
            return {"ok": False, "error": "upload disabled"}
        fn = safe_name(name)
        if fn is None:
            return {"ok": False, "error": "not a photo or video"}
        if length <= 0 or length > MAX_UPLOAD:
            return {"ok": False, "error": "bad size"}
        os.makedirs(self.upload_dir, exist_ok=True)
        same = os.path.join(self.upload_dir, fn)
        if os.path.exists(same) and os.path.getsize(same) == length:
            while length > 0:                                # 读完请求体，连接才能继续用
                chunk = read(min(CHUNK, length))
                if not chunk:
                    break
                length -= len(chunk)
            return {"ok": True, "dup": True, "name": fn}
        tmp = os.path.join(self.upload_dir, f".{secrets.token_hex(6)}.pcupload")
        left = length
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = read(min(CHUNK, left))
                    if not chunk:
                        raise IOError("connection closed")
                    f.write(chunk)
                    left -= len(chunk)
            if mtime_ms and 946684800000 < mtime_ms < (time.time() + 86400) * 1000:
                os.utime(tmp, (mtime_ms / 1000, mtime_ms / 1000))
            final = unique_target(self.upload_dir, fn)
            os.replace(tmp, final)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.received.append(final)
        if self.on_upload:
            try:
                self.on_upload(final)
            except Exception:
                pass
        return {"ok": True, "name": os.path.basename(final)}


class _Handler(BaseHTTPRequestHandler):
    server_version = "PhotoCrate"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:              # 不往控制台刷访问日志
        pass

    @property
    def app(self) -> LanServer:
        return self.server.app                               # type: ignore[attr-defined]

    def _route(self) -> tuple[str, dict] | None:
        u = urllib.parse.urlsplit(self.path)
        parts = u.path.split("/", 2)
        if len(parts) < 2 or not secrets.compare_digest(parts[1], self.app.token):
            return None
        return "/" + (parts[2] if len(parts) > 2 else ""), dict(urllib.parse.parse_qsl(u.query))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith(("text/html", "application/json")) else "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _404(self) -> None:
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _item(self, sid: str) -> Item | None:
        try:
            gid = int(sid)
        except ValueError:
            return None
        return self.app.snapshot.by_gid().get(gid) if self.app.allow_browse else None

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        r = self._route()
        if r is None:
            return self._404()
        if urllib.parse.urlsplit(self.path).path == "/" + self.app.token:     # 补上结尾的 /，相对地址才对
            return self._send(301, b"", "text/plain", {"Location": f"/{self.app.token}/"})
        path, q = r
        app = self.app
        if path == "/":
            from .page import render
            html = render(app.allow_upload, app.allow_browse, app.strings)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'self'; img-src 'self' blob:; media-src 'self'; "
                                                          "style-src 'unsafe-inline'; script-src 'unsafe-inline'"})
        if path == "/api/photos":
            if not app.allow_browse:
                return self._json({"items": [], "albums": []})
            snap = app.snapshot
            return self._json({"items": [{"id": it.gid, "n": it.name, "d": it.date, "v": it.video} for it in snap.items],
                               "albums": [{"k": k, "n": n, "ids": ids} for k, n, ids in snap.albums]})
        kind, _, sid = path.strip("/").partition("/")
        it = self._item(sid) if kind in ("t", "p", "o") else None
        if it is None:
            return self._404()
        if kind == "t":
            b = app.thumb_bytes(it)
            return self._send(200, b, "image/jpeg") if b else self._404()
        if kind == "p" and not it.video:
            b = app.preview_bytes(it)
            return self._send(200, b, "image/jpeg") if b else self._404()
        if kind == "o" or (kind == "p" and it.video):
            return self._file(it, q.get("dl") == "1")
        self._404()

    def _file(self, it: Item, download: bool) -> None:
        """原文件（视频支持 Range）。RAW 不直接给，给 JPEG 大图。"""
        import mimetypes
        if it.role == "raw":
            b = self.app.preview_bytes(it)
            return self._send(200, b, "image/jpeg") if b else self._404()
        try:
            size = os.path.getsize(it.path)
        except OSError:
            return self._404()
        ctype = mimetypes.guess_type(it.path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a:
                    start = int(a)
                    end = min(size - 1, int(b)) if b else size - 1
                else:
                    start = max(0, size - int(b))
                code = 206
            except ValueError:
                start, end = 0, size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(it.name))
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(it.path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass

    def do_PUT(self) -> None:
        r = self._route()
        if r is None or r[0] != "/up":
            return self._404()
        q = r[1]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            mt = float(q.get("mtime") or 0) or None
        except ValueError:
            mt = None
        try:
            res = self.app.receive(q.get("name") or "", length, self.rfile.read, mt)
        except Exception as e:
            self.close_connection = True
            return self._json({"ok": False, "error": str(e)}, 500)
        if not res.get("ok"):
            self.close_connection = True                     # 没读请求体，这个连接不能再用
        self._json(res, 200 if res.get("ok") else 400)
