"""大模型 API Key 和登录令牌的本地保存：单独的文件（不进 settings.json），Windows 上用 DPAPI 加密（只有当前 Windows 用户能解开）。

其他系统没有 DPAPI，退回 base64（只是不明文显示，不算加密），界面上会说明。也可以不保存，改用环境变量。
文件都是原子写入，非 Windows 上权限是 0600。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

ENV = {"claude": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
       "grok": "XAI_API_KEY", "kimi": "MOONSHOT_API_KEY"}


def _path() -> Path:
    from ..config import user_data_dir
    return user_data_dir() / "assistant_keys.json"


def encrypted() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _crypt(fn, data: bytes) -> bytes:
        src, out = _blob(data), _Blob()
        if not fn(ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(out)):   # CRYPTPROTECT_UI_FORBIDDEN
            raise OSError(ctypes.get_last_error() or "DPAPI failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def _protect(data: bytes) -> str:
        return "dpapi:" + base64.b64encode(_crypt(ctypes.windll.crypt32.CryptProtectData, data)).decode("ascii")

    def _unprotect(s: str) -> bytes:
        return _crypt(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(s[6:]))
else:
    def _protect(data: bytes) -> str:
        return "b64:" + base64.b64encode(data).decode("ascii")

    def _unprotect(s: str) -> bytes:
        return base64.b64decode(s[4:])


def _load() -> dict:
    try:
        return json.loads(_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def get_key(provider: str) -> str:
    """保存的 Key 优先，其次环境变量。"""
    v = _load().get(provider)
    if v:
        try:
            return _unprotect(v).decode("utf-8")
        except Exception:
            pass
    return os.environ.get(ENV.get(provider, ""), "").strip()


def key_source(provider: str) -> str:
    """"saved" / "env" / ""。"""
    if _load().get(provider):
        return "saved"
    return "env" if os.environ.get(ENV.get(provider, "")) else ""


def set_key(provider: str, key: str) -> None:
    data = _load()
    key = (key or "").strip()
    if key:
        data[provider] = _protect(key.encode("utf-8"))
    else:
        data.pop(provider, None)
    _write_private(_path(), json.dumps(data, indent=1))


def _write_private(p: Path, text: str) -> None:
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    for attempt in range(20):                # Windows 上别的线程正在读时替换会失败，等一下就好
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _blob_path(name: str) -> Path:
    from ..config import user_data_dir
    return user_data_dir() / f"assistant_{name}.dat"


def get_blob(name: str) -> str:
    """单独一个文件保存的敏感文本（比如登录令牌）。没有或解不开（换了 Windows 用户）都返回空字符串。"""
    try:
        return _unprotect(_blob_path(name).read_text("utf-8").strip()).decode("utf-8")
    except Exception:
        return ""


def set_blob(name: str, text: str) -> None:
    if text:
        _write_private(_blob_path(name), _protect(text.encode("utf-8")))
    else:
        _blob_path(name).unlink(missing_ok=True)
