"""订阅制：调用用户自己安装、自己登录的官方客户端（Claude Code / Codex），用的是他们自己的订阅额度。

为什么不做成“在影匣里登录 Claude / ChatGPT 账号”：
- Anthropic 不允许第三方应用提供 Claude.ai 登录，也不允许收集、保存或转发订阅凭据；但用户用自己的订阅登录未修改的
  Claude Code 是允许的（https://code.claude.com/docs/en/legal-and-compliance）。所以这里只启动客户端，不碰它的凭据文件。
- Codex 同样是用户自己的客户端和登录（`codex login`），影匣同样不读它的 auth.json。

几条必须守住的规则（tests/test_assist_clients.py 逐条检查）：
1. 不读、不存、不转发任何令牌，登录全程在官方客户端自己的窗口和浏览器里完成。
2. 不修改客户端，不带补丁，只用它公开的命令行参数。
3. 选了订阅就真的走订阅：去掉子进程里的 API Key 环境变量（否则客户端会优先用 Key 按量计费），发送前再查一次登录方式，
   不是订阅就不发，免得用户以为在用订阅、实际在扣 API 费用。
4. 客户端只当模型用：关掉工具、会话记录、用户配置，工作目录是空的临时目录。问题走 stdin，不进命令行。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .llm import LLMError, schema

TIMEOUT = 120
STATUS_TIMEOUT = 20
LOGIN_TIMEOUT = 600
_CODEX_OFF = ("shell_tool", "unified_exec", "browser_use", "computer_use", "apps", "plugins", "multi_agent",
              "image_generation", "goals")       # 问答用不到、又能动手的能力，全部关掉
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]*")


@dataclass(frozen=True)
class Client:
    pid: str
    name: str
    exe: str
    plan: str
    models: tuple
    login_args: tuple
    setup_url: str
    strip_env: tuple


CLIENTS: dict[str, Client] = {
    "claude": Client("claude", "Claude Code", "claude", "Claude", ("sonnet", "opus", "haiku"),
                     ("auth", "login", "--claudeai"), "https://code.claude.com/docs/en/setup",
                     ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")),
    "openai": Client("openai", "Codex", "codex", "ChatGPT", ("gpt-6-luna", "gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol"),
                     ("login",), "https://developers.openai.com/codex/cli",
                     ("CODEX_API_KEY", "OPENAI_API_KEY")),
}


@dataclass(frozen=True)
class Status:
    exe: str | None = None
    signed_in: bool = False
    subscription: bool = False
    detail: str = ""             # 给界面看的一句话，比如订阅类型；不含邮箱等账号信息


def model_of(cfg: dict, pid: str) -> str:
    return (cfg.get("client_models") or {}).get(pid) or CLIENTS[pid].models[0]


def path_of(cfg: dict, pid: str) -> str:
    return (cfg.get("client_paths") or {}).get(pid) or ""


def login_command(pid: str) -> str:
    c = CLIENTS[pid]
    return " ".join((c.exe, *c.login_args))


# ---------------------------------------------------------------------------
# 找客户端、运行客户端
# ---------------------------------------------------------------------------

def find_exe(pid: str, override: str = "") -> str | None:
    """用户填的路径优先，其次 PATH，再看常见的安装位置（从资源管理器启动时 PATH 可能不全）。"""
    if override:
        p = Path(override).expanduser()
        return str(p) if p.is_file() else None
    exe = CLIENTS[pid].exe
    found = shutil.which(exe)
    if found:
        return found
    home = Path.home()
    names = (f"{exe}.exe", f"{exe}.cmd") if sys.platform == "win32" else (exe,)
    dirs = [home / ".local" / "bin", home / ".claude" / "local", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    if os.environ.get("APPDATA"):
        dirs.insert(1, Path(os.environ["APPDATA"]) / "npm")
    for d in dirs:
        for n in names:
            if (d / n).is_file():
                return str(d / n)
    return None


def _child_env(pid: str) -> dict:
    env = dict(os.environ)
    for k in CLIENTS[pid].strip_env:
        env.pop(k, None)
    return env


def _kill_tree(p: subprocess.Popen) -> None:
    """npm 装的客户端是 .cmd：只杀 cmd.exe 的话，孙进程还拿着管道，之后读管道会一直卡住。"""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=5)
    except (subprocess.SubprocessError, ValueError):
        pass


def _run(pid: str, exe: str, args: list[str], stdin: str = "", timeout: float = TIMEOUT,
         cwd: str | None = None) -> tuple[int, str, str]:
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        p = subprocess.Popen([exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=cwd, env=_child_env(pid), **kw)
    except OSError as e:
        raise LLMError(f"启动不了 {CLIENTS[pid].name}（{e}）。") from None
    try:
        out, err = p.communicate(stdin.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        raise LLMError(f"等了 {timeout:.0f} 秒 {CLIENTS[pid].name} 没有回应。") from None
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _tail(text: str, n: int = 240) -> str:
    return (text or "").strip()[-n:]


# ---------------------------------------------------------------------------
# 登录状态
# ---------------------------------------------------------------------------

def status(pid: str, override: str = "") -> Status:
    """只问客户端“登录了吗、是不是订阅”，不碰凭据。"""
    exe = find_exe(pid, override)
    if exe is None:
        return Status()
    if pid == "claude":
        _rc, out, err = _run(pid, exe, ["auth", "status"], timeout=STATUS_TIMEOUT)
        try:
            d = json.loads(out)
        except ValueError:
            raise LLMError(f"看不懂 Claude Code 的登录状态，可能版本太旧，请更新。{_tail(err or out)}") from None
        plan = str(d.get("subscriptionType") or "")
        return Status(exe, bool(d.get("loggedIn")), bool(d.get("loggedIn")) and (d.get("authMethod") == "claude.ai" or bool(plan)),
                      plan if plan else str(d.get("authMethod") or ""))
    _rc, out, err = _run(pid, exe, ["login", "status"], timeout=STATUS_TIMEOUT)
    text = f"{out}\n{err}"
    signed = "logged in" in text.lower() and "not logged in" not in text.lower()
    return Status(exe, signed, signed and "chatgpt" in text.lower(), "" if signed else _tail(text, 120))


def _require(pid: str, override: str) -> Status:
    c, st = CLIENTS[pid], status(pid, override)
    if st.exe is None:
        raise LLMError(f"没找到 {c.name} 客户端。先安装（{c.setup_url}），或在设置里填它的路径。")
    if not st.signed_in:
        raise LLMError(f"{c.name} 还没登录。在设置里点「登录」，或在终端运行 {login_command(pid)}。")
    if not st.subscription:
        raise LLMError(f"{c.name} 现在登录的不是 {c.plan} 订阅（{st.detail or 'API Key'}），继续会按用量计费，这次没有发送。"
                       "请重新登录订阅账号，或把连接方式改成 API Key。")
    return st


def open_login(pid: str, override: str = "") -> subprocess.Popen | None:
    """在单独的控制台窗口里跑官方的登录命令。没有弹窗能力的系统返回 None，界面上改为提示命令。"""
    exe = find_exe(pid, override)
    if exe is None or sys.platform != "win32":
        return None
    return subprocess.Popen([exe, *CLIENTS[pid].login_args], env=_child_env(pid),
                            creationflags=subprocess.CREATE_NEW_CONSOLE)


def wait_login(pid: str, proc: subprocess.Popen, override: str = "") -> Status:
    try:
        proc.wait(LOGIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    return status(pid, override)


# ---------------------------------------------------------------------------
# 发请求
# ---------------------------------------------------------------------------

def _friendly(pid: str, message: str) -> LLMError:
    name, m = CLIENTS[pid].name, (message or "").strip()[:240]
    low = m.lower()
    if any(w in low for w in ("usage limit", "rate limit", "limit reached", "hit your limit", "quota")):
        return LLMError(f"{name} 说额度暂时用完了，等额度恢复后再试，或改用 API Key。{m}")
    if any(w in low for w in ("log in", "login", "logged in", "unauthorized", "401", "expired")):
        return LLMError(f"{name} 的登录失效了，在设置里点「登录」重新登录。{m}")
    if "model" in low:
        return LLMError(f"{name} 用不了这个模型，换一个试试。{m}")
    return LLMError(f"{name} 报错：{m or '没有说明原因'}")


def _claude(exe: str, model: str, system_file: str, user: str, image: bytes | None, cwd: str, timeout: float) -> str:
    content = []
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(image).decode("ascii")}})
    content.append({"type": "text", "text": user})
    stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False) + "\n"
    args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--model", model, "--effort", "low", "--system-prompt-file", system_file]
    rc, out, err = _run("claude", exe, args, stdin, timeout, cwd)
    result = None
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            result = d
    if result is None:
        raise _friendly("claude", _tail(err or out) or f"退出码 {rc}")
    if result.get("is_error") or result.get("subtype") != "success":
        raise _friendly("claude", str(result.get("result") or result.get("subtype") or ""))
    return str(result.get("result") or "")


def _codex(exe: str, model: str, system_file: str, user: str, image: bytes | None, cwd: str, timeout: float) -> str:
    schema_file = os.path.join(cwd, "schema.json")
    Path(schema_file).write_text(json.dumps(schema(), ensure_ascii=False), "utf-8")
    instructions = Path(system_file).as_posix()
    args = ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "read-only", "--cd", cwd, "--output-schema", schema_file, "--model", model,
            "-c", f"model_instructions_file='{instructions}'", "-c", "model_reasoning_effort=low",
            "-c", "web_search=disabled"]
    for feature in _CODEX_OFF:
        args += ["--disable", feature]
    if image:
        image_file = os.path.join(cwd, "photo.jpg")
        Path(image_file).write_bytes(image)
        args += ["--image", image_file]
    args += ["--", "-"]
    rc, out, err = _run("openai", exe, args, user, timeout, cwd)
    text, failure = "", ""
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        item = d.get("item") or {}
        if d.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
        elif d.get("type") == "turn.failed":
            failure = str((d.get("error") or {}).get("message") or "")
        elif d.get("type") == "error":
            failure = str(d.get("message") or "")
    if failure and not text:
        raise _friendly("openai", failure)
    if not text:
        raise _friendly("openai", _tail(err or out) or f"退出码 {rc}")
    return text


def complete(pid: str, model: str, override: str, system: str, user: str, image: bytes | None = None,
             timeout: float = TIMEOUT) -> tuple[str, list[str]]:
    """返回 (模型输出的文字, 提示)。不是订阅登录就不发送。"""
    if not _MODEL_RE.fullmatch(model or ""):
        raise LLMError(f"模型名不对：{model}")
    st = _require(pid, override)
    with tempfile.TemporaryDirectory(prefix="pc_assist_") as d:     # 空目录：客户端看不到项目文件，也读不到别处的说明文件
        system_file = os.path.join(d, "system.txt")
        Path(system_file).write_text(system, "utf-8")
        run = _claude if pid == "claude" else _codex
        return run(st.exe, model, system_file, user, image, d, timeout), []


def list_models(pid: str, override: str = "") -> list[str]:
    """Codex 能列出当前账号可用的模型；Claude Code 用它自己的别名（sonnet / opus / haiku），不会过期。"""
    c = CLIENTS[pid]
    exe = find_exe(pid, override)
    if pid != "openai" or exe is None:
        return list(c.models)
    rc, out, _err = _run(pid, exe, ["debug", "models"], timeout=STATUS_TIMEOUT)
    try:
        items = json.loads(out).get("models", [])
    except (ValueError, AttributeError):
        return list(c.models)
    ids = [m["slug"] for m in items if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    return ids if rc == 0 and ids else list(c.models)
