"""测试用的假大模型服务器（只绑 127.0.0.1）：按 Anthropic Messages / OpenAI Responses / chat/completions 三种格式回答，
并记录收到的请求，测试可以检查请求头和请求体。

模型名控制行为：
  bad-key          → 401
  no-such-model    → 404
  busy             → 429
  no-schema        → 带结构化输出参数时返回 400（测试自动去掉重试）
  slow             → 睡 3 秒（测试超时）
  garbage          → 回答不是 JSON
  其他             → 正常回答 REPLY（可以在测试里改）
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = {
    "reply": "曲线在修图的调整图层里。想要通透：拉开黑场、加一点对比。用「曲线」做 S 形，或者「魔术橡皮」。",
    "features": ["curves", "not_a_feature", "levels"],
    "params": [
        {"key": "contrast", "value": 18, "why": "加对比"},
        {"key": "blacks", "value": -400, "why": "压黑（越界，应被夹到 -100）"},
        {"key": "hsl:blue:sat", "value": 20, "why": "天空更蓝"},
        {"key": "dehaze", "value": 30, "why": "影匣没有的参数"},
    ],
    "steps": ["先「选择主体（自动）」", "再「图像 → 调整（新建调整图层） → 曲线」"],
}


class Server:
    def __init__(self):
        self.requests: list[dict] = []
        self.reply = dict(REPLY)
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                srv.requests.append({"path": self.path, "headers": dict(self.headers), "body": None})
                if self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": "model-b"}, {"id": "model-a"}]})
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                srv.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                model = body.get("model", "")
                if model == "bad-key":
                    return self._send(401, {"error": {"message": "invalid x-api-key"}})
                if model == "no-such-model":
                    return self._send(404, {"error": {"message": "model not found"}})
                if model == "busy":
                    return self._send(429, {"error": {"message": "rate limited"}})
                if model == "slow":
                    time.sleep(3)
                structured = any(k in body for k in ("output_config", "response_format", "text"))
                if model == "no-schema" and structured:
                    return self._send(400, {"error": {"message": "response_format is not supported for this model"}})
                text = "抱歉，我不太确定" if model == "garbage" else (
                    "```json\n" + json.dumps(srv.reply, ensure_ascii=False) + "\n```")
                if self.path.endswith("/messages"):
                    return self._send(200, {"type": "message", "model": model, "stop_reason": "end_turn",
                                            "content": [{"type": "thinking", "thinking": ""},
                                                        {"type": "text", "text": text}]})
                if self.path.endswith("/responses"):
                    return self._send(200, {"output": [{"type": "reasoning"},
                                                       {"type": "message", "content": [
                                                           {"type": "output_text", "text": text}]}]})
                if self.path.endswith("/chat/completions"):
                    return self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})
                self._send(404, {"error": {"message": "unknown path"}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------
# 假的 Sign in with ChatGPT：授权页、令牌、吊销、JWKS，以及 /v1/responses（SSE）和 /v1/models。
# 只有这份文件里有私钥，确定性生成，只给测试用。
# ---------------------------------------------------------------------------

_ALL_SCOPES = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
# 文档 preview-limitations 列出的不支持字段：请求里带了就按真实服务那样返回 400
_FORBIDDEN = ("background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent",
              "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation",
              "user", "previous_response_id")
_KEY = None


def _probable_prime(rnd, bits: int) -> int:
    def strong(n):
        d, s = n - 1, 0
        while d % 2 == 0:
            d, s = d // 2, s + 1
        for _ in range(12):
            x = pow(rnd.randrange(2, n - 1), d, n)
            if x in (1, n - 1):
                continue
            for _ in range(s - 1):
                x = x * x % n
                if x == n - 1:
                    break
            else:
                return False
        return True
    while True:
        n = rnd.getrandbits(bits) | (3 << (bits - 2)) | 1
        if all(n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)) and strong(n):
            return n


def _rsa_key() -> tuple[int, int, int]:
    global _KEY
    if _KEY is None:
        rnd = random.Random(20261104)
        e = 65537
        while True:
            p, q = _probable_prime(rnd, 1024), _probable_prime(rnd, 1024)
            phi = (p - 1) * (q - 1)
            if p != q and phi % e:
                _KEY = (p * q, e, pow(e, -1, phi))
                break
    return _KEY


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign(signed: bytes) -> bytes:
    n, _e, d = _rsa_key()
    k = (n.bit_length() + 7) // 8
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signed).digest()
    em = b"\x00\x01" + b"\xff" * (k - 3 - len(info)) + b"\x00" + info
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


class ChatGPTServer:
    """模型名控制 /v1/responses 的行为：
      usage-limit / usage-limit-stream → 额度用完（开始前的 429 / 流里的 response.failed）
      not-eligible → 403；admission-403 / admission-503 → {"detail": ...}；busy-once → 先 503 再正常
      incomplete / cut-off / only-completed / refusal / garbage → 不同的流结尾
      no-schema / no-image → 400 subscription_sharing_unsupported_capability；redirect → 302（不应该跟过去）
    """

    def __init__(self):
        self.requests: list[dict] = []
        self.authorize_queries: list[dict] = []
        self.reply = dict(REPLY)
        self.client_id = "oaiapp_fakeclient1"
        self.subject, self.email = "sub-alice", "alice@example.com"
        self.grant_scope = _ALL_SCOPES
        self.expires_in = 3600
        self.deny = self.omit_registration_client = self.wrong_state_first = False
        self.callback_client = None
        self.id_mode = ""               # bad-sig / alg-none / hs256 / unknown-kid / missing
        self.claims: dict = {}          # 覆盖 ID token 的声明
        self.token_error = None         # (状态码, error) 换码失败
        self.refresh_error = None       # (状态码, error) 刷新失败
        self.refresh_id_token = False   # 刷新时也返回 ID token
        self.revoke_status = 200
        self.jwks_status = 200
        self.earliest_refresh_at = None
        self.valid_access: set[str] = set()
        self.valid_refresh: set[str] = set()
        self.refresh_calls = 0
        self.busy_seen = False
        self.browser_opened = True
        self._codes: dict[str, dict] = {}
        self._serial = 0
        self.models = [{"slug": "gpt-fake", "display_name": "GPT Fake", "visibility": "list"},
                       {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide"},
                       {"slug": "gpt-fast", "display_name": "GPT Fast", "visibility": "list"}]
        self._lock = threading.Lock()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, headers=None):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8") if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("x-request-id", "req_fake")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _events(self, events):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("x-request-id", "req_fake")
                self.end_headers()
                for ev in events:
                    self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

            def _authed(self):
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                if token not in srv.valid_access:
                    self._send(401, {"detail": "The direct permission was not accepted."})
                    return False
                return True

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                srv.requests.append({"path": url.path, "headers": dict(self.headers), "body": None})
                if url.path == "/authorize":
                    return self._authorize(urllib.parse.parse_qs(url.query))
                if url.path == "/jwks":
                    if srv.jwks_status != 200:
                        return self._send(srv.jwks_status, {"error": "unavailable"})
                    n, e, _d = _rsa_key()
                    return self._send(200, {"keys": [
                        {"kty": "EC", "kid": "ignored", "use": "sig", "alg": "ES256"},
                        {"kty": "RSA", "kid": "fake-key", "use": "sig", "alg": "RS256",
                         "n": _b64u(n.to_bytes((n.bit_length() + 7) // 8, "big")), "e": _b64u(e.to_bytes(3, "big"))}]})
                if url.path == "/v1/models":
                    if self._authed():
                        self._send(200, {"models": srv.models})
                    return None
                self._send(404, {"error": {"message": "not found"}})

            def _authorize(self, q):
                one = {k: v[0] for k, v in q.items()}
                srv.authorize_queries.append(one)
                back = {"state": one["state"]}
                if srv.deny:
                    back["error"] = "access_denied"
                else:
                    new = one["client_id"] == "dynamic_agent_client"
                    code = f"code-{len(srv.authorize_queries)}"
                    srv._codes[code] = {**one, "issued": srv.client_id if new else one["client_id"]}
                    back.update({"code": code, "scope": srv.grant_scope})
                    if srv.callback_client is not None:
                        back["client_id"] = srv.callback_client
                    elif new and not srv.omit_registration_client:
                        back["client_id"] = srv.client_id
                loc = one["redirect_uri"] + "?" + urllib.parse.urlencode(back)
                self._send(302, None, {"Location": loc})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                path = urllib.parse.urlsplit(self.path).path
                if path in ("/token", "/revoke"):
                    form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
                    srv.requests.append({"path": path, "headers": dict(self.headers), "body": form})
                    return self._token(form) if path == "/token" else self._revoke(form)
                body = json.loads(raw)
                srv.requests.append({"path": path, "headers": dict(self.headers), "body": body})
                if path == "/v1/responses":
                    return self._responses(body)
                self._send(404, {"error": {"message": "unknown path"}})

            def _revoke(self, form):
                if srv.revoke_status == 200:
                    srv.valid_refresh.discard(form.get("token"))
                    return self._send(200)
                self._send(srv.revoke_status, {"error": "server_error"})

            def _token(self, form):
                grant = form.get("grant_type")
                if grant == "authorization_code":
                    if srv.token_error:
                        return self._send(srv.token_error[0], {"error": srv.token_error[1]})
                    c = srv._codes.pop(form.get("code"), None)
                    want = _b64u(hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest())
                    if (c is None or want != c["code_challenge"] or form.get("redirect_uri") != c["redirect_uri"]
                            or form.get("client_id") != c["issued"] or form.get("resource") != c["resource"]):
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, srv.tokens(c["issued"], c["nonce"], include_id=True))
                if grant == "refresh_token":
                    with srv._lock:
                        srv.refresh_calls += 1
                        time.sleep(0.05)     # 给并发刷新留出撞车的机会
                        if srv.refresh_error:
                            return self._send(srv.refresh_error[0], {"error": srv.refresh_error[1]})
                        if form.get("refresh_token") not in srv.valid_refresh or form.get("resource") is None:
                            return self._send(400, {"error": "refresh_token_reused"})
                        srv.valid_refresh.discard(form["refresh_token"])    # 用一次就换
                        d = srv.tokens(form["client_id"], None, include_id=srv.refresh_id_token)
                    d.pop("scope", None)
                    return self._send(200, d)
                self._send(400, {"error": "unsupported_grant_type"})

            def _responses(self, body):
                if not self._authed():
                    return None
                model = body.get("model", "")
                bad = [k for k in _FORBIDDEN if k in body]
                roles = [m.get("role") for m in body.get("input", []) if isinstance(m, dict)]
                if bad or body.get("store") is not False or body.get("stream") is not True or "system" in roles \
                        or not isinstance(body.get("input"), list):
                    return self._send(400, {"error": {"code": "subscription_sharing_unsupported_capability",
                                                      "param": ",".join(bad) or "store", "message": "not allowed"}})
                if model == "usage-limit":
                    return self._send(429, {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}})
                if model == "not-eligible":
                    return self._send(403, {"error": {"code": "subscription_sharing_user_not_eligible", "message": "no"}})
                if model == "admission-403":
                    return self._send(403, {"detail": "region"})
                if model == "admission-503":
                    return self._send(503, {"detail": "routing disabled"})
                if model == "busy-once" and not srv.busy_seen:
                    srv.busy_seen = True
                    return self._send(503, {"error": {"code": "subscription_sharing_usage_unavailable", "message": "later"}})
                if model == "redirect":
                    return self._send(302, None, {"Location": srv.base + "/stolen"})
                if model == "no-schema" and "text" in body:
                    return self._send(400, {"error": {"code": "subscription_sharing_unsupported_capability",
                                                      "param": "text.format", "message": "unsupported"}})
                has_image = any(c.get("type") == "input_image" for c in body["input"][0]["content"])
                if model == "no-image" and has_image:
                    return self._send(400, {"error": {"code": "subscription_sharing_unsupported_capability",
                                                      "param": "input[0].content[1]", "message": "unsupported"}})
                text = "抱歉，我不太确定" if model == "garbage" else json.dumps(srv.reply, ensure_ascii=False)
                mid = len(text) // 2
                head = [{"type": "response.created", "response": {}},
                        {"type": "response.output_text.delta", "delta": text[:mid]}]
                done_msg = {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
                if model == "usage-limit-stream":
                    return self._events(head + [{"type": "response.failed", "response": {"error": {
                        "code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}])
                if model == "incomplete":
                    return self._events(head + [{"type": "response.incomplete", "response": {
                        "incomplete_details": {"reason": "max_output_tokens"}}}])
                if model == "cut-off":
                    return self._events(head)
                if model == "refusal":
                    return self._events([{"type": "response.refusal.delta", "delta": "no"}])
                if model == "only-completed":
                    return self._events([{"type": "response.completed", "response": done_msg}])
                self._events(head + [{"type": "response.output_text.delta", "delta": text[mid:]},
                                     {"type": "response.completed", "response": done_msg}])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def id_token(self, client_id: str, nonce: str | None) -> str:
        now = int(time.time())
        claims = {"iss": self.base, "sub": self.subject, "aud": [client_id], "exp": now + 3600, "iat": now,
                  "email": self.email}
        if nonce is not None:
            claims["nonce"] = nonce
        claims.update(self.claims)
        head = {"alg": "RS256", "kid": "fake-key", "typ": "JWT"}
        if self.id_mode == "alg-none":
            head["alg"] = "none"
        elif self.id_mode == "hs256":
            head["alg"] = "HS256"
        elif self.id_mode == "unknown-kid":
            head["kid"] = "no-such-key"
        signed = f"{_b64u(json.dumps(head).encode())}.{_b64u(json.dumps(claims).encode())}".encode("ascii")
        sig = _sign(signed)
        if self.id_mode == "bad-sig":
            sig = bytes([sig[0] ^ 1]) + sig[1:]
        return f"{signed.decode('ascii')}.{'' if self.id_mode == 'alg-none' else _b64u(sig)}"

    def tokens(self, client_id: str, nonce: str | None, include_id: bool) -> dict:
        self._serial += 1
        access, refresh = f"access-{self._serial}", f"refresh-{self._serial}"
        self.valid_access = {access}          # 新令牌一出，旧的访问令牌就不能用了
        self.valid_refresh.add(refresh)
        d = {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": self.expires_in,
             "scope": self.grant_scope}
        if self.earliest_refresh_at is not None:
            d["earliest_refresh_at"] = self.earliest_refresh_at
        if include_id and self.id_mode != "missing":
            d["id_token"] = self.id_token(client_id, nonce)
        return d

    def endpoints(self):
        from photocrate.assist.chatgpt import Endpoints
        return Endpoints(self.base, self.base + "/authorize", self.base + "/token", self.base + "/revoke",
                         self.base + "/jwks", self.base + "/v1")

    def browser(self, url: str) -> bool:
        """扮演系统浏览器：用户同意后，授权页 302 回 loopback。真实登录由用户本人在真浏览器里完成。"""
        if not self.browser_opened:
            return False

        def run():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            if self.wrong_state_first:
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                bad = q["redirect_uri"][0] + "?state=attacker&code=stolen&client_id=oaiapp_attacker"
                with contextlib.suppress(urllib.error.HTTPError):
                    opener.open(bad, timeout=5).read()
            with contextlib.suppress(urllib.error.URLError, OSError):
                opener.open(url, timeout=10).read()
        threading.Thread(target=run, daemon=True).start()
        return True

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
