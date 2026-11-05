"""小匣助手的订阅连接（photocrate/assist/clients.py）：用假的客户端（tests/fake_cli.py）测命令、隔离、登录判断和错误提示。

不联网、不需要真的订阅。python -m pytest tests/test_assist_clients.py -q
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))

from photocrate.assist import clients, llm  # noqa: E402

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_cli.py")
KIND = {"claude": "claude", "openai": "codex"}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """生成一个可执行的假客户端（Windows 上是 .cmd，和 npm 装的客户端同一种启动方式）。"""
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    if sys.platform == "win32":
        exe = tmp_path / "fake.cmd"
        exe.write_text(f'@"{sys.executable}" -X utf8 "{FAKE}" %*\r\n', "utf-8")
    else:
        exe = tmp_path / "fake"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', "utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    class F:
        path = str(exe)

        @staticmethod
        def calls():
            return [json.loads(x) for x in log.read_text("utf-8").splitlines()] if log.exists() else []

        @staticmethod
        def use(pid, mode="ok"):
            monkeypatch.setenv("FAKE_CLI_KIND", KIND[pid])
            monkeypatch.setenv("FAKE_CLI_MODE", mode)
    return F


def _complete(fake, pid, user="曲线在哪", image=None, model=None, timeout=30):
    return clients.complete(pid, model or clients.CLIENTS[pid].models[0], fake.path, "系统提示 PC_SYSTEM", user, image, timeout)


# ---------------------------------------------------------------------------

def test_old_config_stays_on_api():
    assert not llm.uses_subscription({"provider": "claude"}, "claude")
    assert not llm.uses_subscription(llm.DEFAULT_CONFIG, "openai")
    assert llm.uses_subscription({"auth": {"openai": "subscription"}}, "openai")
    assert not llm.uses_subscription({"auth": {"grok": "subscription"}}, "grok")        # 只有 Claude / GPT 有订阅连接


def test_claude_command_is_isolated(fake, monkeypatch):
    fake.use("claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak")
    monkeypatch.setenv("KEEP_ME", "1")
    question = '问题 "带引号" & del /q %TEMP%'
    text, _ = _complete(fake, "claude", question, image=b"\xff\xd8jpeg")
    assert json.loads(text)["features"] == ["curves"]
    c = fake.calls()[-1]
    a = c["args"]
    assert a[0] == "-p" and a[a.index("--tools") + 1] == "" and a[a.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in a and "--strict-mcp-config" in a
    assert not any(question in x or "PC_SYSTEM" in x for x in a)                       # 问题和提示词都不进命令行
    assert c["system"] == "系统提示 PC_SYSTEM"
    msg = json.loads(c["stdin"])["message"]
    assert msg["content"][0]["type"] == "image" and msg["content"][-1]["text"] == question
    assert "ANTHROPIC_API_KEY" not in c["env"] and "ANTHROPIC_AUTH_TOKEN" not in c["env"]   # 否则会按 API 计费
    assert c["env"]["KEEP_ME"] == "1"
    assert c["cwd_files"] == ["system.txt"]                                            # 工作目录里没有别的文件


def test_codex_command_is_isolated(fake, monkeypatch):
    fake.use("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "sk-should-not-leak")
    text, _ = _complete(fake, "openai", "想要通透", image=b"\xff\xd8jpeg")
    assert json.loads(text)["reply"]
    c = fake.calls()[-1]
    a = c["args"]
    assert a[0] == "exec" and a[a.index("--sandbox") + 1] == "read-only" and "--ephemeral" in a
    assert "--ignore-user-config" in a and a[-2:] == ["--", "-"]
    for feature in ("shell_tool", "computer_use", "browser_use", "apps", "plugins"):
        assert a[a.index(feature) - 1] == "--disable"
    assert c["stdin"] == "想要通透" and c["system"] == "系统提示 PC_SYSTEM" and c["image"] == 6
    assert json.loads(c["schema"]) == llm.schema()
    assert "OPENAI_API_KEY" not in c["env"] and "CODEX_API_KEY" not in c["env"]


@pytest.mark.parametrize("pid", ["claude", "openai"])
def test_status(fake, pid):
    fake.use(pid)
    st = clients.status(pid, fake.path)
    assert st.exe == fake.path and st.signed_in and st.subscription
    assert "@" not in st.detail                                                         # 账号信息不进界面
    fake.use(pid, "signed_out")
    st = clients.status(pid, fake.path)
    assert st.exe and not st.signed_in and not st.subscription
    fake.use(pid, "api_login")
    st = clients.status(pid, fake.path)
    assert st.signed_in and not st.subscription


@pytest.mark.parametrize("pid", ["claude", "openai"])
@pytest.mark.parametrize("mode,word", [("signed_out", "还没登录"), ("api_login", "按用量计费")])
def test_never_sends_without_subscription(fake, pid, mode, word):
    fake.use(pid, mode)
    with pytest.raises(llm.LLMError, match=word):
        _complete(fake, pid)
    assert fake.calls() == []


def test_missing_client(tmp_path):
    missing = str(tmp_path / "nope.exe")
    assert clients.status("claude", missing).exe is None
    with pytest.raises(llm.LLMError, match="没找到 Claude Code"):
        clients.complete("claude", "sonnet", missing, "s", "u")


@pytest.mark.parametrize("pid", ["claude", "openai"])
def test_errors_are_friendly(fake, pid):
    fake.use(pid, "limit")
    with pytest.raises(llm.LLMError, match="额度"):
        _complete(fake, pid)
    fake.use(pid, "garbage")
    with pytest.raises(llm.LLMError, match="boom"):
        _complete(fake, pid)


def test_timeout_kills_client(fake):
    fake.use("claude", "hang")
    t0 = time.monotonic()
    with pytest.raises(llm.LLMError, match="没有回应"):
        _complete(fake, "claude", timeout=2)
    assert time.monotonic() - t0 < 20                                                   # 杀掉整棵进程树，不会卡在读管道上


@pytest.mark.parametrize("model", ["a&calc", "m m", "", "--tools=Bash", 'x"y'])
def test_bad_model_rejected(fake, model):
    fake.use("claude")
    with pytest.raises(llm.LLMError, match="模型名"):
        clients.complete("claude", model, fake.path, "s", "u")
    assert fake.calls() == []


@pytest.mark.parametrize("pid", ["claude", "openai"])
def test_ask_routes_through_client_and_grounds(fake, pid):
    fake.use(pid)
    cfg = {"provider": pid, "auth": {pid: "subscription"}, "client_paths": {pid: fake.path}, "client_models": {}}
    a = llm.ask(cfg, "", "曲线在哪", None, None, 1)
    assert [f.id for f in a.features] == ["curves"]
    assert "（订阅）" in a.source
    sys_text = fake.calls()[-1]["system"]
    assert "curves | " in sys_text                                                      # 用的是完整的功能目录


def test_api_mode_does_not_touch_client(fake, monkeypatch):
    fake.use("claude")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (json.dumps({"reply": "好", "features": [], "params": [], "steps": []}), []))
    cfg = {"provider": "claude", "auth": {"claude": "api"}, "client_paths": {"claude": fake.path}}
    a = llm.ask(cfg, "sk", "你好", None, None, 0)
    assert a.intro == "好" and fake.calls() == []


def test_model_lists(fake):
    fake.use("openai")
    assert clients.list_models("openai", fake.path) == ["gpt-a"]
    assert clients.list_models("claude", fake.path) == list(clients.CLIENTS["claude"].models)


def test_no_credential_access_in_source():
    """这个模块绝不能碰客户端的凭据文件（Anthropic 明确禁止第三方收集或转发订阅凭据）。"""
    with open(clients.__file__, encoding="utf-8") as f:
        code = f.read().split('"""', 2)[2]
    for word in (".credentials", "auth.json", "CLAUDE_CODE_OAUTH_TOKEN", "access_token", "refresh_token"):
        assert word not in code
