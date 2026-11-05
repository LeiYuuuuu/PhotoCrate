"""小匣助手的 ChatGPT 账号登录（photocrate/assist/chatgpt.py）：用假服务器（tests/fake_llm.py 的 ChatGPTServer）测登录、
令牌校验、刷新、过期、额度用完等。不联网，也不需要真的账号。python -m pytest tests/test_assist_chatgpt.py -q
"""
from __future__ import annotations

import ast
import hashlib
import base64
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm import ChatGPTServer  # noqa: E402
from photocrate import i18n  # noqa: E402
from photocrate.assist import chatgpt, llm, secrets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTOCRATE_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = ChatGPTServer()
    monkeypatch.setattr(chatgpt, "ENDPOINTS", s.endpoints())
    monkeypatch.setattr(chatgpt, "_open_browser", s.browser)
    monkeypatch.setattr(chatgpt, "CALLBACK_PORT", 0)        # 测试不占 1455
    naps: list[float] = []
    monkeypatch.setattr(chatgpt, "_sleep", naps.append)
    s.naps = naps
    s.home = tmp_path
    yield s
    s.close()


def _sign_in(**kw):
    return chatgpt.sign_in(timeout=10, **kw)


def _rec():
    st = chatgpt._load()
    return st["accounts"][st["active"]]


def _paths(s):
    return [r["path"] for r in s.requests]


def _token_grants(s, grant):
    return [r["body"] for r in s.requests if r["path"] == "/token" and r["body"].get("grant_type") == grant]


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

def test_first_sign_in_registers_through_dynamic_client(srv):
    acc = _sign_in()
    assert acc.signed_in and acc.sharing and acc.email == "alice@example.com" and acc.client_id == "oaiapp_fakeclient1"
    q = srv.authorize_queries[0]
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "PhotoCrate"
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["scope"] == "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    assert q["resource"] == "https://api.openai.com/v1"
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/auth/callback", q["redirect_uri"])
    assert "id_token_hint" not in q and "login_hint" not in q and "prompt" not in q
    # 换码用的是签发的 client ID，不是 dynamic_agent_client，也没有 client secret
    form = _token_grants(srv, "authorization_code")[0]
    assert form["client_id"] == "oaiapp_fakeclient1" and form["redirect_uri"] == q["redirect_uri"]
    assert form["resource"] == "https://api.openai.com/v1" and "client_secret" not in form
    assert base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode() == q["code_challenge"]
    rec = _rec()
    for k in ("email", "issuer", "subject", "client_id", "ext_agent_host_id", "id_token", "access_token", "refresh_token",
              "token_type", "expires_in", "scopes", "saved_at"):       # 文档里凭据记录的字段
        assert rec.get(k), k
    assert rec["scopes"] == sorted(rec["scopes"]) and chatgpt.PLAN_SCOPE in rec["scopes"]


def test_each_attempt_gets_fresh_state_nonce_and_pkce(srv):
    _sign_in()
    _sign_in()
    a, b = srv.authorize_queries
    for k in ("state", "nonce", "code_challenge"):
        assert a[k] != b[k], k
        assert len(a[k]) >= 40


def test_host_id_is_an_opaque_uuid_and_persists(srv):
    h = chatgpt.host_id()
    assert re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", h)
    _sign_in()
    srv.client_id = "oaiapp_second"
    _sign_in(new_account=True)
    assert {q["ext_agent_host_id"] for q in srv.authorize_queries} == {h}
    text = next(srv.home.rglob("assistant_chatgpt_host.json")).read_text("utf-8")
    assert "alice" not in text and "sub-" not in text and "@" not in text
    assert chatgpt.host_id() == h


def test_reauthorization_reuses_client_and_sends_hints(srv):
    _sign_in()
    id_token = _rec()["id_token"]
    _sign_in()
    q = srv.authorize_queries[1]
    assert q["client_id"] == "oaiapp_fakeclient1" and "agent_name_hint" not in q
    assert q["id_token_hint"] == id_token and q["login_hint"] == "alice@example.com"
    assert len(chatgpt.accounts()) == 1


def test_tokens_are_not_in_settings_or_plain_files(srv):
    from photocrate.config import Settings
    s = Settings()
    s.set("assistant_llm", {"provider": "openai", "auth": {"openai": "chatgpt"}})
    _sign_in()
    rec = _rec()
    secret_values = [rec["access_token"], rec["refresh_token"], rec["id_token"], rec["email"]]
    files = [p for p in srv.home.rglob("*") if p.is_file()]
    assert files
    for p in files:
        raw = p.read_bytes().decode("utf-8", "replace")
        for v in secret_values:
            assert v not in raw, p
    blob = next(srv.home.rglob("assistant_chatgpt.dat")).read_text("utf-8")
    assert blob.startswith("dpapi:" if secrets.encrypted() else "b64:")
    assert not list(srv.home.rglob("assistant_chatgpt.lock"))          # 锁用完就删


def test_wrong_state_does_not_count_and_real_callback_still_works(srv):
    srv.wrong_state_first = True
    acc = _sign_in()
    assert acc.client_id == "oaiapp_fakeclient1"                       # 不是攻击者塞进来的 oaiapp_attacker
    assert "stolen" not in json.dumps([r["body"] for r in srv.requests if r["path"] == "/token"])


def test_declined_consent_stops_without_exchange(srv):
    srv.deny = True
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "access_denied" and "/token" not in _paths(srv) and chatgpt.current() is None


def test_scope_without_plan_keeps_identity_but_refuses_requests(srv):
    srv.grant_scope = "openid profile email offline_access"
    acc = _sign_in()
    assert acc.signed_in and not acc.sharing
    n = len(srv.requests)
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.code == "plan_not_granted" and len(srv.requests) == n    # 没有发请求
    # 用户主动开启：同一个 client ID、完整 scope、prompt=consent
    srv.grant_scope = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
    acc = _sign_in(reconsent=True)
    q = srv.authorize_queries[-1]
    assert acc.sharing and q["prompt"] == "consent" and "chatgpt.tokens.use.direct" in q["scope"]
    assert q["client_id"] == "oaiapp_fakeclient1"


def test_callback_scope_is_not_trusted_token_response_is(srv, monkeypatch):
    real = srv.tokens

    def fewer(*a, **k):
        d = real(*a, **k)
        d["scope"] = "openid"
        return d
    monkeypatch.setattr(srv, "tokens", fewer)
    assert not _sign_in().sharing                                     # 回跳里的 scope 有计划权限，令牌响应里没有


@pytest.mark.parametrize("omit", ["no-client", "dynamic"])
def test_new_registration_without_issued_client_is_incomplete(srv, omit):
    if omit == "no-client":
        srv.omit_registration_client = True
    else:
        srv.callback_client = "dynamic_agent_client"
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "registration_incomplete" and "/token" not in _paths(srv)


def test_reauthorization_rejects_other_client_and_other_account(srv):
    _sign_in()
    before = dict(_rec())
    srv.callback_client = "oaiapp_other"
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "client_mismatch"
    srv.callback_client = None
    srv.subject = "sub-bob"
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "account_mismatch"
    assert _rec()["refresh_token"] == before["refresh_token"] and _rec()["subject"] == "sub-alice"


def test_issued_client_survives_failed_code_exchange(srv):
    srv.token_error = (400, "invalid_grant")
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "invalid_grant" and chatgpt.current() is None
    srv.token_error = None
    srv.client_id = "oaiapp_should_not_be_issued_again"
    acc = _sign_in()
    assert acc.client_id == "oaiapp_fakeclient1"                       # 下次没有重新注册
    assert srv.authorize_queries[-1]["client_id"] == "oaiapp_fakeclient1"
    assert chatgpt._load()["pending"] == ""


def test_browser_failure_timeout_and_cancel(srv):
    srv.browser_opened = False
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "browser_unavailable"
    chatgpt_open = chatgpt._open_browser
    chatgpt._open_browser = lambda _u: True                            # 浏览器开了，用户一直没操作
    try:
        with pytest.raises(chatgpt.ChatGPTError) as e:
            chatgpt.sign_in(timeout=0.3)
        assert e.value.code == "login_timeout"
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        with pytest.raises(chatgpt.ChatGPTError) as e:
            chatgpt.sign_in(cancel=stop, timeout=20)
        assert e.value.code == "cancelled"
    finally:
        chatgpt._open_browser = chatgpt_open


def test_callback_listener_is_loopback_only_and_strict(srv, monkeypatch):
    seen = {}

    def inspect(url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        cb = q["redirect_uri"][0]
        port = int(urllib.parse.urlsplit(cb).port)
        seen["port"] = port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for bad, status in ((f"http://127.0.0.1:{port}/callback?state={q['state'][0]}&code=x", 404),
                            (f"{cb}?state=nope&code=x", 400),
                            (f"http://localhost:{port}/auth/callback?state={q['state'][0]}&code=x", 404)):
            try:
                opener.open(bad, timeout=5)
                seen[bad] = 200
            except urllib.error.HTTPError as e:
                seen[bad] = e.code
            except urllib.error.URLError:
                seen[bad] = status                                     # localhost 走 IPv6 时连不上，同样不算数
            assert seen[bad] == status, bad
        return srv.browser(url)
    monkeypatch.setattr(chatgpt, "_open_browser", inspect)
    real = chatgpt._CallbackServer
    made = []

    def spy(port, state):
        s = real(port, state)
        made.append(s.server_address[0])
        return s
    monkeypatch.setattr(chatgpt, "_CallbackServer", spy)
    assert _sign_in().sharing
    assert made == ["127.0.0.1"]
    with socket.create_connection(("127.0.0.1", srv.httpd.server_address[1]), timeout=2):
        pass
    with pytest.raises(OSError):                                       # 登录结束，端口已经放开
        socket.create_connection(("127.0.0.1", seen["port"]), timeout=0.5).close()


def test_preferred_port_falls_back_when_busy(srv, monkeypatch):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    monkeypatch.setattr(chatgpt, "CALLBACK_PORT", busy.getsockname()[1])
    try:
        assert _sign_in().sharing
        got = urllib.parse.urlsplit(srv.authorize_queries[0]["redirect_uri"])
        assert got.port != busy.getsockname()[1] and got.path == "/auth/callback" and got.hostname == "127.0.0.1"
    finally:
        busy.close()


# ---------------------------------------------------------------------------
# ID token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["bad-sig", "alg-none", "hs256", "unknown-kid", "missing"])
def test_bad_id_token_signatures_are_rejected(srv, mode):
    srv.id_mode = mode
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "invalid_id_token" and chatgpt.current() is None
    assert not _load_tokens()


def _load_tokens():
    return [r for r in chatgpt._load()["accounts"].values() if r.get("access_token")]


@pytest.mark.parametrize("claims", [{"iss": "https://evil.example"}, {"aud": ["oaiapp_other"]}, {"exp": 1000},
                                    {"nonce": "wrong"}, {"sub": ""}, {"iat": None}])
def test_bad_id_token_claims_are_rejected(srv, claims):
    srv.claims = claims
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.code == "invalid_id_token" and not _load_tokens()


def test_jwks_outage_does_not_replace_connection(srv):
    _sign_in()
    before = _rec()["access_token"]
    chatgpt._JWKS.clear()
    srv.jwks_status = 503
    with pytest.raises(chatgpt.ChatGPTError) as e:
        _sign_in()
    assert e.value.temporary and _rec()["access_token"] == before


def test_rs256_rejects_short_and_oversized_signatures():
    from fake_llm import _rsa_key, _sign
    n, e, _d = _rsa_key()
    sig = _sign(b"hello")
    assert chatgpt._rs256_ok(n, e, b"hello", sig)
    assert not chatgpt._rs256_ok(n, e, b"hellO", sig)
    assert not chatgpt._rs256_ok(n, e, b"hello", sig[1:])
    k = (n.bit_length() + 7) // 8
    assert not chatgpt._rs256_ok(n, e, b"hello", (int.from_bytes(sig, "big") + n).to_bytes(k + 1, "big"))


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------

def test_complete_follows_preview_rules(srv):
    _sign_in()
    token = _rec()["access_token"]
    text, notes = chatgpt.complete("gpt-fake", "系统提示", "你好", b"\xff\xd8jpeg")
    assert json.loads(text)["reply"] and notes == []
    req = [r for r in srv.requests if r["path"] == "/v1/responses"][-1]
    b = req["body"]
    assert b["store"] is False and b["stream"] is True and b["instructions"] == "系统提示" and b["model"] == "gpt-fake"
    assert isinstance(b["input"], list) and b["input"][0]["role"] == "user"
    assert b["input"][0]["content"][1]["type"] == "input_image"
    assert b["text"]["format"]["type"] == "json_schema"
    assert {k.lower(): v for k, v in req["headers"].items()}["authorization"] == f"Bearer {token}"


def test_ask_goes_through_ground_and_never_uses_api_key(srv, monkeypatch):
    _sign_in()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    cfg = {"provider": "openai", "auth": {"openai": "chatgpt"}, "chatgpt_model": "gpt-fake"}
    a = llm.ask(cfg, "sk-should-not-be-used", "曲线在哪", None, None, 1)
    assert a.source == "GPT · gpt-fake · ChatGPT plan"
    assert [f.id for f in a.features] == ["curves", "levels"]          # 不认识的 id 被 ground() 丢掉
    assert a.plan.params["blacks"] == -100                             # 越界夹回
    assert any("dehaze" in n for n in a.notes)
    assert not any("sk-should" in json.dumps(r["headers"]) for r in srv.requests)


@pytest.mark.parametrize("model,code,action", [
    ("usage-limit", "subscription_sharing_usage_limit_exceeded", "manage_usage"),
    ("usage-limit-stream", "subscription_sharing_usage_limit_exceeded", "manage_usage"),
    ("not-eligible", "subscription_sharing_user_not_eligible", ""),
])
def test_plan_errors_stop_without_retry(srv, model, code, action):
    _sign_in()
    n = len([r for r in srv.requests if r["path"] == "/v1/responses"])
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete(model, "s", "u")
    assert e.value.code == code and e.value.action == action
    assert len([r for r in srv.requests if r["path"] == "/v1/responses"]) == n + 1     # 只发了一次，没有循环
    assert _rec()["access_token"] and srv.naps == []                   # 令牌没动，也没有等待重试
    assert not re.search(r"\d+ ?(小时|分钟|hour|minute)", str(e.value))   # 不猜恢复时间


def test_usage_limit_points_to_manage_usage(srv):
    _sign_in()
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("usage-limit", "s", "u")
    assert str(e.value).startswith("Usage limit reached") and chatgpt.USAGE_URL == "https://chatgpt.com/settings/usage"


def test_admission_errors_without_error_object(srv):
    _sign_in()
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("admission-403", "s", "u")
    assert e.value.status == 403 and "region" in str(e.value) and "req_fake" in str(e.value)
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("admission-503", "s", "u")
    assert e.value.status == 503 and e.value.temporary and srv.naps == [1.0, 3.0] and _rec()["access_token"]


def test_temporary_unavailability_uses_bounded_backoff(srv):
    _sign_in()
    text, _ = chatgpt.complete("busy-once", "s", "u")
    assert json.loads(text)["reply"] and srv.naps == [1.0]


@pytest.mark.parametrize("model,code", [("incomplete", "response_incomplete"), ("cut-off", "stream_interrupted")])
def test_success_only_on_response_completed(srv, model, code):
    _sign_in()
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete(model, "s", "u")
    assert e.value.code == code


def test_stream_edge_cases(srv):
    _sign_in()
    assert json.loads(chatgpt.complete("only-completed", "s", "u")[0])["reply"]    # 只有 completed 也能取到文字
    with pytest.raises(llm.LLMError):
        chatgpt.complete("refusal", "s", "u")
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.complete("bad model name!", "s", "u")
    with pytest.raises(llm.LLMError):                                  # 回答不是 JSON：和别的后端一样退回本地
        llm.ask({"provider": "openai", "auth": {"openai": "chatgpt"}, "chatgpt_model": "garbage"}, "", "q", None, None, 0)


def test_unsupported_capability_is_removed_not_resent(srv):
    _sign_in()
    text, notes = chatgpt.complete("no-image", "s", "u", b"\xff\xd8jpeg")
    assert json.loads(text)["reply"] and notes                         # 去掉图片再发
    reqs = [r["body"] for r in srv.requests if r["path"] == "/v1/responses"]
    assert len(reqs[-1]["input"][0]["content"]) == 1
    text, _ = chatgpt.complete("no-schema", "s", "u")
    reqs = [r["body"] for r in srv.requests if r["path"] == "/v1/responses"]
    assert "text" in reqs[-2] and "text" not in reqs[-1]               # 去掉结构化输出再发


def test_redirects_are_not_followed_with_the_token(srv):
    _sign_in()
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("redirect", "s", "u")
    assert e.value.status == 302 and "/stolen" not in _paths(srv)


def test_list_models_keeps_server_order_and_visible_only(srv):
    _sign_in()
    assert chatgpt.list_models() == [("GPT Fake", "gpt-fake"), ("GPT Fast", "gpt-fast")]


# ---------------------------------------------------------------------------
# 刷新、过期
# ---------------------------------------------------------------------------

def _expire(store_seconds=-10):
    with chatgpt._exclusive():
        st = chatgpt._load()
        st["accounts"][st["active"]]["expires_at"] = time.time() + store_seconds
        chatgpt._save(st)


def test_expired_access_token_is_refreshed_and_rotated(srv):
    _sign_in()
    old = _rec()
    _expire()
    assert json.loads(chatgpt.complete("gpt-fake", "s", "u")[0])["reply"]
    form = _token_grants(srv, "refresh_token")[-1]
    assert form == {"grant_type": "refresh_token", "client_id": "oaiapp_fakeclient1",
                    "refresh_token": old["refresh_token"], "resource": "https://api.openai.com/v1"}   # 没有 scope
    new = _rec()
    assert new["access_token"] != old["access_token"] and new["refresh_token"] != old["refresh_token"]
    assert new["expires_at"] > time.time() + 1000 and new["scopes"] == old["scopes"]   # 响应没带 scope 时保留


def test_near_expiry_refreshes_but_fresh_token_does_not(srv):
    _sign_in()
    chatgpt.complete("gpt-fake", "s", "u")
    assert srv.refresh_calls == 0
    _expire(chatgpt.REFRESH_MARGIN - 5)
    chatgpt.complete("gpt-fake", "s", "u")
    assert srv.refresh_calls == 1


def test_concurrent_requests_refresh_once(srv):
    _sign_in()
    _expire()
    out, errs = [], []

    def go():
        try:
            out.append(chatgpt.complete("gpt-fake", "s", "u"))
        except Exception as e:                                          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs and len(out) == 6 and srv.refresh_calls == 1       # refresh token 用一次就换，不能撞车


@pytest.mark.parametrize("code", sorted(chatgpt._TERMINAL_REFRESH))
def test_terminal_refresh_error_clears_tokens_keeps_registration(srv, code):
    _sign_in()
    host = chatgpt.host_id()
    _expire()
    srv.refresh_error = (400, code)
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.action == "sign_in" and e.value.code == code
    rec = _rec()
    assert not rec.get("access_token") and not rec.get("refresh_token")
    assert rec["client_id"] == "oaiapp_fakeclient1" and rec["subject"] == "sub-alice" and chatgpt.host_id() == host
    assert not chatgpt.current().signed_in
    srv.refresh_error = None
    _sign_in()                                                          # 重新登录不用重新注册
    assert srv.authorize_queries[-1]["client_id"] == "oaiapp_fakeclient1"


def test_invalid_client_keeps_credentials(srv):
    _sign_in()
    _expire()
    srv.refresh_error = (401, "invalid_client")
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.code == "invalid_client" and _rec()["refresh_token"]


def test_network_or_server_failure_keeps_credentials(srv, monkeypatch):
    _sign_in()
    keep = _rec()["refresh_token"]
    _expire()
    srv.refresh_error = (500, "server_error")
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.temporary and _rec()["refresh_token"] == keep
    srv.refresh_error = None
    monkeypatch.setattr(chatgpt, "ENDPOINTS", chatgpt.Endpoints(srv.base, "", "http://127.0.0.1:9/token", "", "", srv.base + "/v1"))
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.temporary and _rec()["refresh_token"] == keep
    # 快过期但还没过期：刷新失败不影响这次请求
    monkeypatch.setattr(chatgpt, "ENDPOINTS", srv.endpoints())
    _expire(30)
    srv.refresh_error = (500, "server_error")
    assert json.loads(chatgpt.complete("gpt-fake", "s", "u")[0])["reply"]


def test_rejected_token_refreshes_once_then_stops(srv):
    _sign_in()
    srv.valid_access.clear()                                            # 服务器不再认这个访问令牌
    assert json.loads(chatgpt.complete("gpt-fake", "s", "u")[0])["reply"] and srv.refresh_calls == 1
    srv.valid_access.clear()
    srv.refresh_error = (400, "refresh_token_invalidated")              # 用户在 ChatGPT 设置里断开了
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.action == "sign_in" and not _rec().get("access_token")


def test_earliest_refresh_at_is_respected(srv):
    srv.earliest_refresh_at = int(time.time()) + 600
    _sign_in()
    _expire(30)
    assert json.loads(chatgpt.complete("gpt-fake", "s", "u")[0])["reply"] and srv.refresh_calls == 0   # 还能用，先不刷新
    _expire(-10)
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.code == "refresh_too_early" and _rec()["refresh_token"]


def test_refreshed_id_token_is_checked(srv):
    _sign_in()
    _expire()
    srv.refresh_id_token = True
    chatgpt.complete("gpt-fake", "s", "u")
    assert _rec()["id_token"] and not _rec().get("unverified_id_token")
    _expire()
    srv.subject = "sub-mallory"
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.action == "sign_in" and not _rec().get("access_token")


def test_refresh_checkpoint_survives_jwks_outage(srv):
    _sign_in()
    _expire()
    srv.refresh_id_token = True
    chatgpt._JWKS.clear()
    srv.jwks_status = 503
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.temporary
    rec = _rec()
    assert rec["unverified_id_token"] and rec["refresh_token"] in srv.valid_refresh     # 新令牌已落盘，旧的不能再用
    calls = srv.refresh_calls
    srv.jwks_status = 200
    assert json.loads(chatgpt.complete("gpt-fake", "s", "u")[0])["reply"]
    assert srv.refresh_calls == calls and not _rec().get("unverified_id_token")         # 接着验，没有再刷新


# ---------------------------------------------------------------------------
# 账号和退出
# ---------------------------------------------------------------------------

def test_sign_out_revokes_and_keeps_registration(srv):
    _sign_in()
    rec = _rec()
    assert chatgpt.sign_out() is True
    form = [r["body"] for r in srv.requests if r["path"] == "/revoke"][-1]
    assert form == {"token": rec["refresh_token"], "token_type_hint": "refresh_token", "client_id": rec["client_id"]}
    after = _rec()
    for k in ("access_token", "refresh_token", "id_token"):
        assert k not in after
    assert after["client_id"] == rec["client_id"] and not chatgpt.current().signed_in
    _sign_in()
    q = srv.authorize_queries[-1]
    assert q["client_id"] == rec["client_id"] and "id_token_hint" not in q   # 退出后不带 id_token_hint


def test_sign_out_reports_unconfirmed_revocation_but_clears_locally(srv):
    _sign_in()
    srv.revoke_status = 503
    assert chatgpt.sign_out() is False and srv.naps == [0.0, 1.0, 3.0]
    assert "access_token" not in _rec()


def test_accounts_stay_separate(srv):
    _sign_in()
    first = _rec()
    srv.client_id, srv.subject, srv.email = "oaiapp_bob", "sub-bob", "bob@example.com"
    assert _sign_in(new_account=True).email == "bob@example.com"
    assert srv.authorize_queries[-1]["client_id"] == "dynamic_agent_client"
    assert chatgpt.current().client_id == "oaiapp_bob" and len(chatgpt.accounts()) == 2
    assert first["access_token"] != _rec()["access_token"]
    assert len({a.label for a in chatgpt.accounts()}) == 2
    chatgpt.select(first["client_id"])
    assert chatgpt.current().email == "alice@example.com"


def test_welcome_is_remembered(srv):
    assert not chatgpt.welcomed()
    chatgpt.set_welcomed()
    assert chatgpt.welcomed()


def test_corrupt_or_undecryptable_store_means_signed_out(srv):
    _sign_in()
    next(srv.home.rglob("assistant_chatgpt.dat")).write_text("dpapi:AAAA", "utf-8")
    assert chatgpt.current() is None
    with pytest.raises(chatgpt.ChatGPTError) as e:
        chatgpt.complete("gpt-fake", "s", "u")
    assert e.value.action == "sign_in"


# ---------------------------------------------------------------------------
# 静态检查
# ---------------------------------------------------------------------------

def _code_strings(path: Path):
    """去掉文档字符串后的所有字符串常量。"""
    tree = ast.parse(path.read_text("utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node, clean=False):
            docs.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_does_not_touch_codex_credentials():
    strings = _code_strings(ROOT / "photocrate" / "assist" / "chatgpt.py")
    assert not any("auth.json" in s or ".codex" in s or "backend-api" in s for s in strings)
    assert not any(s.startswith("app_") for s in strings)              # 没有写死别的应用的 client ID


def test_official_endpoints():
    e = chatgpt.OPENAI
    assert e.issuer == "https://auth.openai.com" and e.api == "https://api.openai.com/v1"
    assert e.authorize == "https://auth.openai.com/api/accounts/authorize"
    assert e.token == "https://auth.openai.com/api/accounts/oauth/token"
    assert e.revoke == "https://auth.openai.com/api/accounts/oauth/revoke"
    assert e.jwks == "https://auth.openai.com/.well-known/jwks.json"
    assert chatgpt.ENDPOINTS is e or os.environ.get("PYTEST_CURRENT_TEST")


def test_required_strings_are_shown_verbatim():
    src = (ROOT / "photocrate" / "ui" / "assistant_llm.py").read_text("utf-8")
    for s in ("Continue with ChatGPT", "Using ChatGPT plan", "Manage usage", "You're using your ChatGPT plan", "Got it"):
        assert f'"{s}"' in src, s
    assert (ROOT / "assets" / "chatgpt-logo-white.svg").is_file()
    assert "Usage limit reached" in (ROOT / "photocrate" / "ui" / "assistant.py").read_text("utf-8")


def test_every_new_string_has_english():
    i18n.set_language("en")
    try:
        missing = []
        for rel in ("assist/chatgpt.py", "ui/assistant_llm.py"):
            tree = ast.parse((ROOT / "photocrate" / rel).read_text("utf-8"))
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_t" and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                        and n.args[0].value not in i18n._EN):
                    missing.append(n.args[0].value)
        assert not missing, missing
    finally:
        i18n.set_language("zh")
