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

import json
import threading
import time
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
