"""测试用的假 Claude Code / Codex 客户端：不联网，把收到的参数、stdin、工作目录、环境变量记到 FAKE_CLI_LOG 指定的文件。

用 FAKE_CLI_KIND（claude / codex）选客户端，FAKE_CLI_MODE 控制行为：
  ok         正常回答（默认）
  signed_out 没登录
  api_login  登录的是 API Key，不是订阅
  limit      额度用完
  garbage    回答不是 JSON 事件
  hang       一直不退出（测试超时）
"""
import json
import os
import sys
import time

KIND, MODE = os.environ.get("FAKE_CLI_KIND", "claude"), os.environ.get("FAKE_CLI_MODE", "ok")
ANSWER = json.dumps({"reply": "曲线在调整图层里", "features": ["curves"], "params": [], "steps": []}, ensure_ascii=False)
args = sys.argv[1:]


def log(**kw):
    path = os.environ.get("FAKE_CLI_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def is_status():
    return args[:2] == ["auth", "status"] or args[:2] == ["login", "status"]


if is_status():
    if KIND == "claude":
        emit({"loggedIn": MODE != "signed_out", "authMethod": "console" if MODE == "api_login" else "claude.ai",
              "subscriptionType": None if MODE == "api_login" else "max", "email": "secret@example.com"})
        sys.exit(1 if MODE == "signed_out" else 0)
    msg = {"signed_out": "Not logged in", "api_login": "Logged in using an API key"}.get(MODE, "Logged in using ChatGPT")
    sys.stderr.write(msg + "\n")
    sys.exit(1 if MODE == "signed_out" else 0)

if args[:2] == ["debug", "models"]:
    emit({"models": [{"slug": "gpt-a", "visibility": "list"}, {"slug": "gpt-hidden", "visibility": "hide"}]})
    sys.exit(0)

stdin = sys.stdin.buffer.read().decode("utf-8")
extra = {}
for flag, name in (("--system-prompt-file", "system"), ("--output-schema", "schema")):
    if flag in args:
        extra[name] = open(args[args.index(flag) + 1], encoding="utf-8").read()
if "--image" in args:
    extra["image"] = os.path.getsize(args[args.index("--image") + 1])
for a in args:
    if a.startswith("model_instructions_file="):
        extra["system"] = open(a.split("=", 1)[1].strip("'"), encoding="utf-8").read()
log(args=args, stdin=stdin, cwd=os.getcwd(), cwd_files=sorted(os.listdir(os.getcwd())),
    env={k: os.environ[k] for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
                                    "CLAUDECODE", "KEEP_ME") if k in os.environ}, **extra)

if MODE == "hang":
    time.sleep(60)
if MODE == "garbage":
    print("完全不是 JSON")
    sys.stderr.write("boom\n")
    sys.exit(3)
if KIND == "claude":
    emit({"type": "system", "subtype": "init", "tools": []})
    if MODE == "limit":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your limit · resets 3pm"})
        sys.exit(1)
    emit({"type": "result", "subtype": "success", "is_error": False, "result": ANSWER})
else:
    emit({"type": "thread.started", "thread_id": "x"})
    if MODE == "limit":
        emit({"type": "turn.failed", "error": {"message": "usage limit reached"}})
        sys.exit(1)
    emit({"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": ANSWER}})
    emit({"type": "turn.completed", "usage": {}})
