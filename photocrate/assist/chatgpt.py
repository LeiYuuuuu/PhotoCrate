"""在影匣里用 ChatGPT 账号登录（OpenAI 的 Sign in with ChatGPT · ChatGPT plan usage），回答消耗用户自己的 ChatGPT 计划额度。

官方文档：https://developers.openai.com/siwc/token-sharing-open-source
不装 Codex，也不读 Codex 的 auth.json、不用它的 client ID：第一次登录用 client_id=dynamic_agent_client 注册，
OpenAI 给这个用户签发影匣自己的 client ID（oaiapp_...），之后都用它。不需要 client secret 或 API Key。
只有用户自己在系统浏览器里登录和授权，影匣从头到尾看不到密码。Claude 不能这样做：Anthropic 禁止第三方应用接 Claude.ai 登录。

几条必须守住的规则（tests/test_assist_chatgpt.py 逐条检查）：
1. 回调只绑 127.0.0.1，每次登录重新生成 state / nonce / PKCE；state 不对的请求不算数。
2. 令牌只在换码成功、ID token 的签名 / iss / aud / exp / nonce 都通过之后才保存；回来的账号和所选账号不一致就不替换。
3. 授予的 scope 里没有 chatgpt.tokens.use.direct 就不发请求（身份有效不等于同意使用计划）。
4. 令牌用 secrets.py 单独保存（Windows 上 DPAPI），不进 settings.json，不进日志、错误信息和 URL；
   Authorization 头只发给 OpenAI 官方地址，遇到重定向就报错，不跟过去。
5. 请求只走 POST /v1/responses，store=false、stream=true，读到 response.completed 才算成功。
6. 额度用完等错误就地停下并说明，绝不悄悄改走 API Key；只有确认令牌失效才清除，断网之类的暂时错误不清除。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..i18n import t as _t
from . import secrets
from .llm import BY_PID, LLMError, _text_of, schema

APP_NAME = "PhotoCrate"                      # agent_name_hint：应用的真实名字，所有安装都一样
DYNAMIC_CLIENT = "dynamic_agent_client"      # 只是首次注册的入口，不保存、也不用来换令牌
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = ("openid", "profile", "email", "offline_access", "resource.invoke", PLAN_SCOPE)
RESOURCE = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
CALLBACK_PATH = "/auth/callback"
CALLBACK_PORT = 1455                         # 被占用时换一个空闲端口（文档允许变的只有端口）
MODELS = ("gpt-6-luna", "gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol")     # 没登录时的默认列表，登录后向账号查

LOGIN_TIMEOUT = 600
TIMEOUT = 120
HTTP_TIMEOUT = 30
REFRESH_MARGIN = 120                         # 访问令牌还剩这么多秒就刷新
CLOCK_SKEW = 5
BACKOFF = (1.0, 3.0)                         # 暂时不可用时最多再试两次

_TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                     "refresh_token_invalidated", "refresh_token_reused"}
_TEMPORARY = {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*")
_CLIENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}")
_HOST_RE = re.compile(r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")   # PKCS#1 v1.5 里 SHA-256 的前缀


@dataclass(frozen=True)
class Endpoints:
    issuer: str
    authorize: str
    token: str
    revoke: str
    jwks: str
    api: str


OPENAI = Endpoints("https://auth.openai.com", "https://auth.openai.com/api/accounts/authorize",
                   "https://auth.openai.com/api/accounts/oauth/token", "https://auth.openai.com/api/accounts/oauth/revoke",
                   "https://auth.openai.com/.well-known/jwks.json", RESOURCE)
ENDPOINTS = OPENAI                           # 只有测试会换成假服务器；界面上不能改，令牌只发给 OpenAI


class ChatGPTError(LLMError):
    """message 给用户看，不含令牌。action：manage_usage = 主操作是打开用量设置；sign_in = 要重新登录。"""

    def __init__(self, message: str, *, code: str = "", status: int | None = None, param: str = "", detail: str = "",
                 request_id: str = "", action: str = "", temporary: bool = False):
        super().__init__(message)
        self.code, self.status, self.param, self.detail = code, status, param, detail
        self.request_id, self.action, self.temporary = request_id, action, temporary


@dataclass(frozen=True)
class Account:
    client_id: str
    email: str
    subject: str
    signed_in: bool
    sharing: bool                  # 有令牌，并且用户授予了使用 ChatGPT 计划的权限

    @property
    def label(self) -> str:
        """邮箱可能重复（同一账号的不同工作区），带上 client ID 末尾几位来区分。"""
        return f"{self.email or self.subject[:12]} · …{self.client_id[-4:]}"


def model_of(cfg: dict) -> str:
    return cfg.get("chatgpt_model") or MODELS[0]


# ---------------------------------------------------------------------------
# 保存：host ID（不是秘密，也不含用户信息）单独一个文件；令牌和账号信息加密保存
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()


def _host_path():
    from ..config import user_data_dir
    return user_data_dir() / "assistant_chatgpt_host.json"


def host_id() -> str:
    """这台电脑上这份影匣的 ext_agent_host_id：只生成一次，换账号、重新登录都不变。"""
    with _LOCK:
        try:
            v = json.loads(_host_path().read_text("utf-8")).get("ext_agent_host_id", "")
            if _HOST_RE.fullmatch(v):
                return v
        except (OSError, ValueError, AttributeError):
            pass
        v = f"urn:uuid:{uuid.uuid4()}"
        secrets._write_private(_host_path(), json.dumps({"ext_agent_host_id": v}))
        return v


def _load() -> dict:
    try:
        d = json.loads(secrets.get_blob("chatgpt") or "{}")
    except ValueError:
        d = {}
    d.setdefault("active", "")
    d.setdefault("pending", "")
    d.setdefault("accounts", {})
    return d


def _save(store: dict) -> None:
    secrets.set_blob("chatgpt", json.dumps(store, ensure_ascii=False))


@contextlib.contextmanager
def _exclusive(timeout: float = 45.0):
    """刷新令牌的锁：refresh token 用一次就换，两个线程或两个影匣窗口不能同时刷新。"""
    from ..config import user_data_dir
    path = user_data_dir() / "assistant_chatgpt.lock"
    deadline = time.monotonic() + timeout
    with _LOCK:
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - path.stat().st_mtime > 90:     # 上次崩溃留下的
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise ChatGPTError(_t("另一个影匣窗口正在更新 ChatGPT 登录，稍后再试。"), temporary=True) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(fd)
            with contextlib.suppress(OSError):
                path.unlink()


def _account(rec: dict) -> Account:
    signed = bool(rec.get("access_token"))
    return Account(rec.get("client_id", ""), rec.get("email", ""), rec.get("subject", ""), signed,
                   signed and PLAN_SCOPE in (rec.get("scopes") or []))


def accounts() -> list[Account]:
    return [_account(r) for r in _load()["accounts"].values()]


def current() -> Account | None:
    s = _load()
    rec = s["accounts"].get(s["active"])
    return _account(rec) if rec else None


def select(client_id: str) -> None:
    """切换到另一个保存过的账号。两个账号的令牌各管各的。"""
    with _exclusive():
        s = _load()
        if client_id in s["accounts"]:
            s["active"] = client_id
            _save(s)


def welcomed() -> bool:
    """第一次登录时的说明只显示一次。"""
    return bool(_load().get("welcomed"))


def set_welcomed() -> None:
    with _exclusive():
        s = _load()
        s["welcomed"] = True
        _save(s)


# ---------------------------------------------------------------------------
# HTTP：不跟随重定向（urllib 默认会把 Authorization 头带去新地址）
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):
        return None


def _open(req: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout)     # 走系统代理


def _unreachable(reason) -> ChatGPTError:
    return ChatGPTError(_t("连不上 ChatGPT（{0}）。在国内通常需要代理。", reason), temporary=True)


class _OAuthFail(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


def _error_fields(body: str) -> tuple[str, str, str]:
    """返回 (code, param, message)。错误体可能是 {"error": {...}}、{"error": "code"} 或 {"detail": "..."}。"""
    try:
        d = json.loads(body)
    except ValueError:
        return "", "", ""
    if not isinstance(d, dict):
        return "", "", ""
    e = d.get("error")
    if isinstance(e, dict):
        return str(e.get("code") or e.get("type") or ""), str(e.get("param") or ""), str(e.get("message") or "")[:200]
    if isinstance(e, str):
        return e, "", str(d.get("error_description") or "")[:200]
    return str(d.get("code") or ""), "", str(d.get("detail") or d.get("message") or "")[:200]


def _form(url: str, fields: dict) -> dict:
    """OAuth 的表单请求。HTTP 错误抛 _OAuthFail，网络错误抛 ChatGPTError(temporary)。"""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("ascii"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                                          "User-Agent": "PhotoCrate-Assistant"}, method="POST")
    try:
        with _open(req, HTTP_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", "replace")
        except Exception:
            body = ""
        raise _OAuthFail(_error_fields(body)[0], e.code) from None
    except (OSError, http.client.HTTPException) as e:
        raise _unreachable(getattr(e, "reason", None) or type(e).__name__) from None
    if not raw.strip():
        return {}                                   # 吊销成功就是空的 200
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ChatGPTError(_t("ChatGPT 登录服务返回的内容看不懂，稍后再试。"), temporary=True) from None
    if not isinstance(d, dict):
        raise ChatGPTError(_t("ChatGPT 登录服务返回的内容看不懂，稍后再试。"), temporary=True)
    return d


# ---------------------------------------------------------------------------
# ID token 校验：只用标准库。公开的是验证运算，不涉及私钥，自己写 RS256 验证是安全的
# ---------------------------------------------------------------------------

_JWKS: dict[tuple[str, str], tuple[int, int]] = {}


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _rs256_ok(n: int, e: int, signed: bytes, sig: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(sig) != k or int.from_bytes(sig, "big") >= n or k < 3 + len(_DIGEST_INFO) + 32 + 8:
        return False
    want = (b"\x00\x01" + b"\xff" * (k - 3 - len(_DIGEST_INFO) - 32) + b"\x00" + _DIGEST_INFO
            + hashlib.sha256(signed).digest())
    return hmac.compare_digest(pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big"), want)


def _key(kid: str) -> tuple[int, int]:
    url = ENDPOINTS.jwks
    if (url, kid) not in _JWKS:                     # 换了新密钥才重新取
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PhotoCrate-Assistant"})
        try:
            with _open(req, HTTP_TIMEOUT) as r:
                keys = json.loads(r.read().decode("utf-8"))["keys"]
            for k in keys:
                if k.get("kty") == "RSA" and k.get("kid") and k.get("alg", "RS256") == "RS256" and k.get("use", "sig") == "sig":
                    n, exp = int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big")
                    if n.bit_length() >= 2048 and exp >= 3 and exp % 2:
                        _JWKS[(url, k["kid"])] = (n, exp)
        except (OSError, http.client.HTTPException, ValueError, AttributeError, KeyError, TypeError) as e:
            raise ChatGPTError(_t("现在无法验证 ChatGPT 返回的身份信息（{0}），连接信息没有改动，稍后再试。", type(e).__name__),
                               temporary=True) from None
    if (url, kid) not in _JWKS:
        raise _bad_identity("kid")
    return _JWKS[(url, kid)]


def _bad_identity(why: str) -> ChatGPTError:
    return ChatGPTError(_t("ChatGPT 返回的身份信息没有通过验证（{0}），这次登录没有保存。", why), code="invalid_id_token")


def verify_id_token(token: str, client_id: str, nonce: str | None, now: float | None = None) -> dict:
    """校验签名、iss、aud、exp、iat、nonce，返回 claims。nonce=None 只用于刷新（刷新的响应里没有 nonce）。"""
    now = time.time() if now is None else now
    try:
        if not token.isascii():
            raise ValueError
        head64, body64, sig64 = token.split(".")
        head, claims, sig = json.loads(_b64(head64)), json.loads(_b64(body64)), _b64(sig64)
    except (ValueError, AttributeError):
        raise _bad_identity("format") from None
    if not isinstance(head, dict) or not isinstance(claims, dict):
        raise _bad_identity("format")
    if head.get("alg") != "RS256" or not head.get("kid") or not isinstance(head["kid"], str):      # 拒绝 none、HS256 等
        raise _bad_identity("alg")
    n, e = _key(head["kid"])
    if not _rs256_ok(n, e, f"{head64}.{body64}".encode("ascii"), sig):
        raise _bad_identity("signature")
    if claims.get("iss") != ENDPOINTS.issuer:
        raise _bad_identity("iss")
    aud = claims.get("aud")
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        raise _bad_identity("aud")
    exp, iat = claims.get("exp"), claims.get("iat")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now - CLOCK_SKEW:
        raise _bad_identity("exp")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)):
        raise _bad_identity("iat")
    if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")).encode("utf-8"), nonce.encode("utf-8")):
        raise _bad_identity("nonce")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise _bad_identity("sub")
    return claims


# ---------------------------------------------------------------------------
# 登录：loopback 回调 + 系统浏览器
# ---------------------------------------------------------------------------

_PAGE = ("<!doctype html><html lang='zh'><meta charset='utf-8'><title>影匣 PhotoCrate</title>"
         "<style>body{{font:16px system-ui,sans-serif;max-width:30rem;margin:18vh auto;padding:24px;color:#202123}}</style>"
         "<h2>{0}</h2><p>{1}</p></html>")


class _CallbackServer(ThreadingHTTPServer):
    allow_reuse_address = False                 # Windows 上 SO_REUSEADDR 会让别的程序也能绑同一个端口
    daemon_threads = True
    block_on_close = False

    def __init__(self, port: int, state: str):
        self.state, self.result = state, None
        self.got = threading.Event()
        super().__init__(("127.0.0.1", port), _CallbackHandler)

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)    # 不走 HTTPServer.server_bind：它会反查主机名，有些机器要等好几秒
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class _CallbackHandler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *_a):                 # URL 里有授权码，不记日志
        pass

    def _page(self, code: int, title: str, text: str) -> None:
        data = _PAGE.format(title, text).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        srv = self.server
        url = urllib.parse.urlsplit(self.path)
        if url.path != CALLBACK_PATH or self.headers.get("Host") != f"127.0.0.1:{srv.server_port}":
            return self._page(404, "404", "")
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        one = {k: v[0] for k, v in q.items() if len(v) == 1}
        if not hmac.compare_digest(one.get("state", "").encode("utf-8"), srv.state.encode("utf-8")):
            # 可能是别的网页在试探本机端口：不算数，继续等真正的回跳
            return self._page(400, _t("这不是这次登录的回跳"), _t("请回到影匣重新点一次登录。"))
        if srv.result is None:
            srv.result = one
            srv.got.set()
        self._page(200, _t("可以回到影匣了"), _t("影匣会完成连接检查，这个页面可以关掉。"))


def _listen(state: str) -> _CallbackServer:
    try:
        return _CallbackServer(CALLBACK_PORT, state)
    except OSError:
        return _CallbackServer(0, state)        # 1455 被占用（比如 Codex 正在登录）


def _open_browser(url: str) -> bool:            # 测试里换成假浏览器
    return webbrowser.open(url)


def authorization_url(*, client_id: str | None, host: str, redirect_uri: str, state: str, nonce: str, challenge: str,
                      id_token_hint: str = "", login_hint: str = "", reconsent: bool = False) -> str:
    q = {"client_id": client_id or DYNAMIC_CLIENT}
    if client_id is None:
        q["agent_name_hint"] = APP_NAME         # 只在首次注册时带
    q["ext_agent_host_id"] = host
    if id_token_hint:
        q["id_token_hint"] = id_token_hint
    if login_hint:
        q["login_hint"] = login_hint
    if reconsent:
        q["prompt"] = "consent"                 # 只在用户主动开启计划用量时才强制授权页
    q.update({"response_type": "code", "redirect_uri": redirect_uri, "scope": " ".join(SCOPES), "resource": RESOURCE,
              "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge})
    return f"{ENDPOINTS.authorize}?{urllib.parse.urlencode(q, quote_via=urllib.parse.quote)}"


def _token_fields(data: dict, fallback_scopes=None) -> dict:
    token = data.get("access_token")
    if not isinstance(token, str) or not token or str(data.get("token_type", "bearer")).lower() != "bearer":
        raise ChatGPTError(_t("ChatGPT 没有返回可用的令牌，请重新登录。"), code="invalid_token_response")
    try:
        expires_in = int(data.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    now = time.time()
    scope = data.get("scope")
    f = {"access_token": token, "token_type": "Bearer", "expires_in": expires_in, "expires_at": now + expires_in,
         "scopes": sorted(scope.split()) if isinstance(scope, str) else list(fallback_scopes or []),
         "saved_at": datetime.now(timezone.utc).isoformat(), "earliest_refresh_at": data.get("earliest_refresh_at")}
    if isinstance(data.get("refresh_token"), str) and data["refresh_token"]:
        f["refresh_token"] = data["refresh_token"]
    return f


def sign_in(*, new_account: bool = False, reconsent: bool = False, client_id: str | None = None,
            cancel: threading.Event | None = None, timeout: float = LOGIN_TIMEOUT) -> Account:
    """打开系统浏览器让用户登录，等回跳。默认用当前账号的 client ID；new_account=True 注册新账号。在后台线程里调用。"""
    store = _load()
    rec = None
    if client_id:
        rec = store["accounts"].get(client_id)
    elif not new_account:
        rec = store["accounts"].get(store["active"])
    issued = (rec or {}).get("client_id") or client_id or store["pending"] or None   # 上次注册成功但换码失败的，继续用
    state, nonce, verifier = _b64e(os.urandom(32)), _b64e(os.urandom(32)), _b64e(os.urandom(48))
    challenge = _b64e(hashlib.sha256(verifier.encode("ascii")).digest())
    host = host_id()
    server = _listen(state)
    redirect_uri = f"http://127.0.0.1:{server.server_port}{CALLBACK_PATH}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    try:
        url = authorization_url(client_id=issued, host=host, redirect_uri=redirect_uri, state=state, nonce=nonce,
                                challenge=challenge, id_token_hint=(rec or {}).get("id_token", ""),
                                login_hint=(rec or {}).get("email", ""), reconsent=reconsent)
        if not _open_browser(url):
            raise ChatGPTError(_t("打不开系统浏览器，这次没有登录。"), code="browser_unavailable")
        deadline = time.monotonic() + timeout
        while not server.got.wait(0.1):
            if cancel is not None and cancel.is_set():
                raise ChatGPTError(_t("已取消登录。"), code="cancelled")
            if time.monotonic() > deadline:
                raise ChatGPTError(_t("等了 {0:.0f} 分钟没有完成登录，这次没有保存。", timeout / 60), code="login_timeout")
        cb = server.result
    finally:
        server.shutdown()
        server.server_close()
    if cb.get("error"):
        if cb["error"] == "access_denied":
            raise ChatGPTError(_t("你在浏览器里没有同意授权，这次没有连接。可以再试一次，或改用 API Key。"), code="access_denied")
        raise ChatGPTError(_t("ChatGPT 登录失败：{0}", str(cb.get("error_description") or cb["error"])[:200]), code=str(cb["error"]))
    got = cb.get("client_id", "")
    if issued is None:
        if not got or got == DYNAMIC_CLIENT or not _CLIENT_RE.fullmatch(got):
            raise ChatGPTError(_t("ChatGPT 没有返回 client ID，注册没有完成，请重试。"), code="registration_incomplete")
        issued = got
        with _exclusive():                       # 换码可能失败：先记下，下次不用重新注册
            s = _load()
            s["pending"] = issued
            _save(s)
    elif got and got != issued:
        raise ChatGPTError(_t("回跳里的 client ID 和所选账号不一致，这次登录没有保存。"), code="client_mismatch")
    code = cb.get("code")
    if not code:
        raise ChatGPTError(_t("ChatGPT 没有返回授权码，请重试。"), code="no_code")
    try:
        data = _form(ENDPOINTS.token, {"grant_type": "authorization_code", "client_id": issued, "code": code,
                                       "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE})
    except _OAuthFail as e:
        if e.code == "invalid_grant":
            raise ChatGPTError(_t("授权码已失效，请再点一次 Continue with ChatGPT。"), code=e.code, status=e.status) from None
        raise ChatGPTError(_t("ChatGPT 登录失败（{0} {1}）。", e.status, e.code), code=e.code, status=e.status) from None
    fields = _token_fields(data)
    id_token = data.get("id_token")
    if not isinstance(id_token, str):
        raise _bad_identity("missing")
    claims = verify_id_token(id_token, issued, nonce)
    if rec is not None and rec.get("subject") and claims["sub"] != rec["subject"]:
        raise ChatGPTError(_t("登录的账号和所选的不是同一个，原来的连接没有改动。要加新账号请点「使用其他账号」。"), code="account_mismatch")
    new = {**(rec or {}), **fields, "issuer": ENDPOINTS.issuer, "subject": claims["sub"], "client_id": issued,
           "email": str(claims.get("email") or (rec or {}).get("email") or ""), "ext_agent_host_id": host, "id_token": id_token}
    new.pop("unverified_id_token", None)
    with _exclusive():
        s = _load()
        s["accounts"][issued] = new
        s["active"] = issued
        if s["pending"] == issued:
            s["pending"] = ""
        _save(s)
    return _account(new)


# ---------------------------------------------------------------------------
# 刷新和退出
# ---------------------------------------------------------------------------

def _sign_in_again(message: str, code: str = "", status: int | None = None) -> ChatGPTError:
    return ChatGPTError(message, code=code, status=status, action="sign_in")


def _clear_tokens(rec: dict, keep_id_token: bool) -> None:
    for k in ("access_token", "refresh_token", "unverified_id_token", "unverified_at", "expires_at", "expires_in",
              "earliest_refresh_at", "saved_at", "token_type"):
        rec.pop(k, None)
    if not keep_id_token:
        rec.pop("id_token", None)


def _earliest(rec: dict) -> float:
    v = rec.get("earliest_refresh_at")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    return 0.0


def _settle(store: dict, rec: dict) -> None:
    """刷新已经换掉了旧 refresh token：新令牌先落盘，再验身份。网络断了下次接着验；验过才能用。"""
    token = rec.get("unverified_id_token")
    if not token:
        return
    try:
        claims = verify_id_token(token, rec["client_id"], None, rec.get("unverified_at"))
        if claims["sub"] != rec.get("subject"):
            raise _bad_identity("sub")
    except ChatGPTError as e:
        if e.temporary:
            raise
        _clear_tokens(rec, keep_id_token=False)
        _save(store)
        raise _sign_in_again(_t("ChatGPT 刷新后的身份和这个账号对不上，请重新登录。"), e.code) from None
    rec["id_token"] = token
    rec.pop("unverified_id_token", None)
    rec.pop("unverified_at", None)
    _save(store)


def _fresh(force_replacing: str | None = None) -> dict:
    """返回当前账号可用的记录，快过期就刷新。force_replacing：这个访问令牌被服务器拒绝了，必须换掉（别的线程换过就不用再换）。"""
    with _exclusive():
        store = _load()
        rec = store["accounts"].get(store["active"])
        if not rec or not rec.get("access_token"):
            raise _sign_in_again(_t("还没有连接 ChatGPT，请在助手设置里点 Continue with ChatGPT。"), "not_signed_in")
        _settle(store, rec)
        now = time.time()
        expires = float(rec.get("expires_at") or 0)
        rejected = force_replacing is not None and force_replacing == rec["access_token"]
        if not rejected and now < expires - REFRESH_MARGIN:
            return rec
        usable = not rejected and now < expires

        def temporary(err: ChatGPTError) -> dict:
            if usable:                             # 还没真正过期：先用着，下次再刷新
                return rec
            raise err

        if not rec.get("refresh_token"):
            if usable:
                return rec
            _clear_tokens(rec, keep_id_token=True)
            _save(store)
            raise _sign_in_again(_t("ChatGPT 登录已过期，请重新点 Continue with ChatGPT。"), "token_expired")
        if _earliest(rec) > now:
            return temporary(ChatGPTError(_t("ChatGPT 登录暂时不能更新，稍后再试。"), code="refresh_too_early", temporary=True))
        try:
            data = _form(ENDPOINTS.token, {"grant_type": "refresh_token", "client_id": rec["client_id"],
                                           "refresh_token": rec["refresh_token"], "resource": RESOURCE})
            fields = _token_fields(data, rec.get("scopes"))
        except ChatGPTError as e:
            return temporary(e)
        except _OAuthFail as e:
            if e.code in _TERMINAL_REFRESH or (e.status == 401 and not e.code):
                _clear_tokens(rec, keep_id_token=True)     # 令牌确实不能用了；client ID 和账号留着，下次免注册
                _save(store)
                raise _sign_in_again(_t("ChatGPT 登录已失效（可能在 ChatGPT 设置里断开了连接），请重新点 Continue with ChatGPT。"),
                                     e.code, e.status) from None
            if e.code == "invalid_client":
                raise ChatGPTError(_t("ChatGPT 不认这个 client ID（invalid_client）。可以点「使用其他账号」重新注册。"),
                                   code=e.code, status=e.status) from None
            return temporary(ChatGPTError(_t("ChatGPT 登录服务暂时出错（{0}），连接信息没有改动，稍后再试。", e.status),
                                          code=e.code, status=e.status, temporary=True))
        rec.pop("earliest_refresh_at", None)
        rec.update(fields)                         # 访问令牌、到期时间、scope、新的 refresh token 一起换
        id_token = data.get("id_token")
        if isinstance(id_token, str) and id_token:
            rec["unverified_id_token"], rec["unverified_at"] = id_token, time.time()
        _save(store)
        _settle(store, rec)
        return rec


def _require_plan() -> dict:
    rec = _fresh()
    if PLAN_SCOPE not in (rec.get("scopes") or []):
        raise ChatGPTError(_t("这个 ChatGPT 账号还没授权影匣使用计划额度，这次没有发送。请在助手设置里重新授权，或改用 API Key。"),
                           code="plan_not_granted", action="sign_in")
    return rec


def sign_out(client_id: str | None = None) -> bool:
    """吊销 refresh token 再清掉本地令牌，保留 client ID 和 host ID。返回是否确认吊销成功。"""
    with _exclusive():
        store = _load()
        rec = store["accounts"].get(client_id or store["active"])
        if not rec:
            return True
        token, cid = rec.get("refresh_token"), rec.get("client_id", "")
        _clear_tokens(rec, keep_id_token=False)    # 先停用：之后不管吊销成不成功，本地都不再有令牌
        rec.pop("scopes", None)
        _save(store)
    if not token:
        return True
    for delay in (0.0, *BACKOFF):
        _sleep(delay)
        try:
            _form(ENDPOINTS.revoke, {"token": token, "token_type_hint": "refresh_token", "client_id": cid})
            return True
        except ChatGPTError:
            continue
        except _OAuthFail as e:
            if e.status < 500:
                return False
    return False


def _sleep(seconds: float) -> None:            # 测试里换掉，不真的等
    if seconds > 0:
        time.sleep(seconds)


# ---------------------------------------------------------------------------
# 请求：POST /v1/responses，流式读到 response.completed
# ---------------------------------------------------------------------------

def _explain(status: int | None, code: str, param: str, message: str, request_id: str) -> ChatGPTError:
    kw = {"code": code, "status": status, "param": param, "detail": message, "request_id": request_id}
    tail = f" {message}" if message else ""
    tail += _t("（请求编号 {0}）", request_id) if request_id else ""
    if code == "subscription_sharing_usage_limit_exceeded":
        return ChatGPTError(_t("Usage limit reached：你的 ChatGPT 计划或影匣这个应用的用量上限到了，请到 ChatGPT 设置里查看。"),
                            action="manage_usage", **kw)
    if code == "subscription_sharing_user_not_eligible":
        return ChatGPTError(_t("这个账号或工作区不能在这里使用 ChatGPT 计划（需要符合条件的 Plus / Pro 计划）。可以换账号或改用 API Key。") + tail, **kw)
    if code in _TEMPORARY:
        return ChatGPTError(_t("ChatGPT 暂时查不到用量信息，连接信息没有改动，稍后再试。") + tail, temporary=True, **kw)
    if code == "subscription_sharing_unsupported_capability":
        return ChatGPTError(_t("这个模型或这种输入不支持计划用量（{0}）。可以换一个模型。", param or "?") + tail, **kw)
    if code == "subscription_sharing_route_not_supported":
        return ChatGPTError(_t("ChatGPT 不允许这个接口使用计划额度，请更新影匣。") + tail, **kw)
    if code in ("chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"):
        return ChatGPTError(_t("这次授权不允许这个操作，请退出后重新授权。") + tail, **kw)
    if code == "subscription_sharing_invalid_user" or status == 401:
        return ChatGPTError(_t("ChatGPT 没有接受这个账号的身份。连接信息没有改动，可以稍后再试，或退出后重新登录。") + tail, **kw)
    if status == 403:
        return ChatGPTError(_t("ChatGPT 拒绝了这次请求，可能是所在地区或账号策略的限制。") + tail, **kw)
    if status == 404:
        return ChatGPTError(_t("这个账号用不了这个模型，可以点「刷新」看看有哪些模型。") + tail, **kw)
    if status == 429:
        return ChatGPTError(_t("请求太频繁了，稍后再试。") + tail, temporary=True, **kw)
    if status is not None and status >= 500:
        return ChatGPTError(_t("ChatGPT 暂时不可用（{0}），连接信息没有改动，稍后再试。", status) + tail, temporary=True, **kw)
    return ChatGPTError(_t("ChatGPT 报错（{0}）。", status or code or "?") + tail, **kw)


def _http_error(e: urllib.error.HTTPError) -> ChatGPTError:
    try:
        body = e.read(4096).decode("utf-8", "replace")
    except Exception:
        body = ""
    code, param, message = _error_fields(body)
    return _explain(e.code, code, param, message, (e.headers.get("x-request-id") or "") if e.headers else "")


def _request(model: str, system: str, user: str, image: bytes | None, structured: bool) -> dict:
    content = [{"type": "input_text", "text": user}]
    if image:
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")})
    # 预览期不支持 max_output_tokens、temperature、metadata 等字段，也不接受 role=system 的消息（见 preview-limitations）
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": content}],
            "store": False, "stream": True}
    if structured:
        body["text"] = {"format": {"type": "json_schema", "name": "photocrate_answer", "schema": schema(), "strict": True}}
    return body


def _stream(token: str, body: dict, timeout: float) -> str:
    req = urllib.request.Request(ENDPOINTS.api + "/responses", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Accept": "text/event-stream", "User-Agent": "PhotoCrate-Assistant"}, method="POST")
    deadline = time.monotonic() + timeout
    interrupted = ChatGPTError(_t("回答中途断开了，没有收到完整结果，稍后再试。"), code="stream_interrupted", temporary=True)
    try:
        r = _open(req, timeout)
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None
    except (socket.timeout, TimeoutError):
        raise ChatGPTError(_t("等了 {0:.0f} 秒没有回应，网络可能不通。", timeout), code="timeout", temporary=True) from None
    except urllib.error.URLError as e:
        raise _unreachable(e.reason) from None
    parts: list[str] = []
    completed = None
    with r:
        rid = r.headers.get("x-request-id") or ""
        try:
            for raw in r:
                if time.monotonic() > deadline:
                    raise ChatGPTError(_t("等了 {0:.0f} 秒没有回应，网络可能不通。", timeout), code="timeout", temporary=True)
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                kind = d.get("type")
                if kind == "response.output_text.delta" and isinstance(d.get("delta"), str):
                    parts.append(d["delta"])
                elif kind in ("response.refusal.delta", "response.refusal.done"):
                    raise LLMError("模型拒绝回答这个问题。")
                elif kind == "response.failed":
                    e = (d.get("response") or {}).get("error") or {}
                    raise _explain(None, str(e.get("code") or ""), str(e.get("param") or ""), str(e.get("message") or "")[:200], rid)
                elif kind == "error":
                    e = d.get("error") if isinstance(d.get("error"), dict) else d
                    raise _explain(None, str(e.get("code") or ""), str(e.get("param") or ""), str(e.get("message") or "")[:200], rid)
                elif kind == "response.incomplete":
                    why = ((d.get("response") or {}).get("incomplete_details") or {}).get("reason") or "?"
                    raise ChatGPTError(_t("回答没有写完（{0}），这次不算数。", why), code="response_incomplete", request_id=rid)
                elif kind == "response.completed":
                    completed = d.get("response") or {}
                    break
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException):
            raise interrupted from None
    if completed is None:
        raise interrupted
    text = "".join(parts) or _text_of(BY_PID["openai"], completed)     # 只收到 completed、没有增量也算
    if not text.strip():
        raise ChatGPTError(_t("模型的回答是空的。"), code="empty", request_id=rid)
    return text


def complete(model: str, system: str, user: str, image: bytes | None = None, timeout: float = TIMEOUT) -> tuple[str, list[str]]:
    """返回 (模型输出的文字, 提示)。401 刷新一次；暂时不可用有限次退避；不支持的参数去掉后再发。别的错误就地停下。"""
    if not _MODEL_RE.fullmatch(model or ""):
        raise ChatGPTError(_t("模型名不对：{0}", model), code="bad_model")
    notes: list[str] = []
    structured, refreshed, waits = True, False, list(BACKOFF)
    for _attempt in range(8):
        rec = _require_plan()
        try:
            return _stream(rec["access_token"], _request(model, system, user, image, structured), timeout), notes
        except ChatGPTError as e:
            if e.status == 401 and not refreshed:  # 令牌可能刚被吊销：刷新一次才能确认
                refreshed = True
                _fresh(force_replacing=rec["access_token"])
                continue
            if waits and (e.code in _TEMPORARY or e.status == 503):
                _sleep(waits.pop(0))
                continue
            where = f"{e.param} {e.detail}".lower()         # 用服务器的原文判断，界面语言不影响
            if e.status == 400 or e.code == "subscription_sharing_unsupported_capability":
                if image is not None and (e.param.startswith("input") or any(w in where for w in ("image", "vision"))):
                    image = None
                    notes.append(_t("这个模型不支持看图，这次只发了文字。"))
                    continue
                if structured and any(w in where for w in ("text", "format", "schema")):
                    structured = False             # 退回提示词约束 + 本地校验
                    continue
            raise
    raise ChatGPTError(_t("请求失败。"), code="retries_exhausted")


def list_models() -> list[tuple[str, str]]:
    """当前账号能用的模型，(显示名, slug)，保持服务器的顺序。"""
    rec = _require_plan()
    for retry in (True, False):
        req = urllib.request.Request(ENDPOINTS.api + "/models", headers={"Authorization": f"Bearer {rec['access_token']}",
                                                                          "User-Agent": "PhotoCrate-Assistant"})
        try:
            with _open(req, HTTP_TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            err = _http_error(e)
            if e.code == 401 and retry:
                rec = _fresh(force_replacing=rec["access_token"])
                continue
            raise err from None
        except ValueError:
            raise ChatGPTError(_t("ChatGPT 返回的模型列表看不懂。"), code="bad_models") from None
        except (OSError, http.client.HTTPException) as e:
            raise _unreachable(getattr(e, "reason", None) or type(e).__name__) from None
    items = d.get("models") if isinstance(d, dict) else None
    return [(str(m.get("display_name") or m["slug"]), str(m["slug"])) for m in items or []
            if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
