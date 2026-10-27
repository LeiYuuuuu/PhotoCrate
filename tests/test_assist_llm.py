"""小匣助手的大模型后端（photocrate/assist/llm.py）：请求格式、重试、错误提示、对模型输出的校验、Key 的保存。

用本机的假服务器（tests/fake_llm.py），不需要真的 API Key、不联网。python -m pytest tests/test_assist_llm.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PHOTOCRATE_HOME", tempfile.mkdtemp(prefix="pc_test_home_"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm import Server  # noqa: E402
from photocrate.assist import llm, secrets  # noqa: E402
from photocrate.assist.engine import Assistant  # noqa: E402
from photocrate.assist.features import FEATURES  # noqa: E402
from photocrate.edit import adjust  # noqa: E402


@pytest.fixture(scope="module")
def srv():
    s = Server()
    yield s
    s.close()


def _cfg(pid, model, srv):
    return {"provider": pid, "models": {pid: model}, "base_urls": {pid: srv.base}, "send_image": False}


# ---------------------------------------------------------------------------

def test_prompt_and_schema_cover_knowledge_base():
    sp = llm.system_prompt()
    for f in FEATURES:
        assert f"{f.id} | {f.title}" in sp
    for k in adjust.SLIDERS:
        assert f"\n{k} | " in sp
    sch = llm.schema()
    assert sch["properties"]["features"]["items"]["enum"] == [f.id for f in FEATURES]
    assert set(sch["properties"]["params"]["items"]["properties"]["key"]["enum"]) == set(llm.PARAM_KEYS)
    assert "hsl:blue:sat" in llm.PARAM_KEYS
    assert llm.system_prompt() is sp                    # 固定不变，服务商才能缓存


@pytest.mark.parametrize("pid", [p.id for p in llm.PROVIDERS])
def test_each_provider_roundtrip(srv, pid):
    srv.requests.clear()
    local = Assistant().ask("太灰了想通透")
    a = llm.ask(_cfg(pid, "m1", srv), "sk-test", "太灰了想通透，曲线在哪", local, None, 1,
                [("亮一点", "曝光 +0.27")], image=b"\xff\xd8fakejpeg")
    req = srv.requests[-1]
    body, h = req["body"], {k.lower(): v for k, v in req["headers"].items()}
    kind = llm.BY_PID[pid].kind
    if kind == "anthropic":
        assert req["path"] == "/v1/messages"
        assert h["x-api-key"] == "sk-test" and h["anthropic-version"] == llm.ANTHROPIC_VERSION
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["messages"][0]["content"][0]["type"] == "image"
        assert not any(k in body for k in ("temperature", "top_p", "top_k"))
    else:
        assert h["authorization"] == "Bearer sk-test"
        if kind == "responses":
            assert req["path"] == "/v1/responses" and body["text"]["format"]["strict"] is True
            assert body["input"][0]["content"][1]["type"] == "input_image"
        else:
            assert req["path"] == "/v1/chat/completions" and body["messages"][0]["role"] == "system"
            assert body["response_format"]["type"] in ("json_schema", "json_object")
    user = json.dumps(body, ensure_ascii=False)
    assert "本地助手初稿" in user and "亮一点" in user          # 带上了本地初稿和上一轮
    # 校验
    assert a.kind == "llm" and a.source == f"{llm.BY_PID[pid].name} · m1"
    assert [f.id for f in a.features] == ["curves", "levels"]          # 不认识的 id 丢掉
    p = a.plan.params
    assert p["contrast"] == 18 and p["blacks"] == -100 and p["hsl"]["blue"] == [0, 20, 0]
    assert any("dehaze" in n for n in a.notes)                         # 没有的参数说明已忽略
    assert any("魔术橡皮" in n for n in a.notes)                       # 编造的界面名称被标出来
    assert not any("选择主体" in n for n in a.notes)
    assert a.recipes and len(a.recipes[0].steps) == 2
    adjust.develop(__import__("numpy").full((2, 2, 3), 0.5, "float32"), p)


def test_retry_without_structured_output(srv):
    srv.requests.clear()
    a = llm.ask(_cfg("grok", "no-schema", srv), "k", "曲线在哪", None, None, 1)
    assert len(srv.requests) == 2 and "response_format" not in srv.requests[-1]["body"]
    assert a.features[0].id == "curves"


@pytest.mark.parametrize("model,words", [("bad-key", "API Key 无效"), ("no-such-model", "刷新"), ("busy", "429"),
                                         ("garbage", "JSON")])
def test_errors_are_explained(srv, model, words):
    with pytest.raises(llm.LLMError) as e:
        llm.ask(_cfg("deepseek", model, srv), "k", "曲线在哪", None, None, 0)
    assert words in str(e.value)


def test_timeout_and_unreachable(srv):
    with pytest.raises(llm.LLMError) as e:
        llm.complete("kimi", "slow", "k", "s", "u", base=srv.base, timeout=0.5)
    assert "秒" in str(e.value) or "连不上" in str(e.value)
    with pytest.raises(llm.LLMError) as e:
        llm.complete("claude", "m", "k", "s", "u", base="http://127.0.0.1:9/v1", timeout=2)
    assert "连不上" in str(e.value)
    with pytest.raises(llm.LLMError):
        llm.complete("claude", "m", "", "s", "u", base=srv.base)


def test_list_models(srv):
    assert llm.list_models("openai", "k", srv.base) == ["model-a", "model-b"]


def test_parse_and_ground_edge_cases():
    assert llm.parse_json('好的：\n{"reply": "x", "features": [], "params": [], "steps": []}\n以上') ["reply"] == "x"
    with pytest.raises(llm.LLMError):
        llm.parse_json("[1, 2]")
    with pytest.raises(llm.LLMError):
        llm.ground({"reply": "", "features": ["nope"], "params": [], "steps": []}, "X · y")
    a = llm.ground({"reply": "只能回答影匣的问题", "features": [], "params": [{"key": "exposure", "value": "abc"}],
                    "steps": []}, "X · y")
    assert a.plan is None and any("exposure" in n for n in a.notes)
    assert llm.unknown_names("「图像 → 调整（新建调整图层） → 曲线」「选择主体（自动）」") == []
    assert llm.unknown_names("用「AI 消除笔」") == ["AI 消除笔"]


def test_user_prompt_has_stats_but_no_paths():
    from photocrate.assist.photo import PhotoStats
    st = PhotoStats(0.4, 0.1, 0.9, 0.01, 0.0, 0.2, -20, 3, 0.2)
    u = llm.user_prompt("这张怎么修", None, st, 1)
    assert "平均亮度 40%" in u and "色温 -20" in u and "修图" in u


def test_secrets_roundtrip(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    secrets.set_key("deepseek", "sk-abc-123")
    raw = (secrets._path()).read_text("utf-8")
    assert "sk-abc-123" not in raw                                     # 不明文保存
    if secrets.encrypted():
        assert "dpapi:" in raw
    assert secrets.get_key("deepseek") == "sk-abc-123" and secrets.key_source("deepseek") == "saved"
    secrets.set_key("deepseek", "")
    assert secrets.get_key("deepseek") == "" and secrets.key_source("deepseek") == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert secrets.get_key("deepseek") == "sk-env" and secrets.key_source("deepseek") == "env"
