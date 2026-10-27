"""大模型 API Key 的本地保存：单独一个文件（不进 settings.json），Windows 上用 DPAPI 加密（只有当前 Windows 用户能解开）。

其他系统没有 DPAPI，退回 base64（只是不明文显示，不算加密），界面上会说明。也可以不保存，改用环境变量。
"""
from __future__ import annotations

import base64
import json
import os
import sys
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
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), "utf-8")
    os.replace(tmp, p)
