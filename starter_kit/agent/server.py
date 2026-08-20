#!/usr/bin/env python3
"""LoomQ L2 本地 UI 服务。

用法：
    uv run python -m starter_kit.agent.server
    然后浏览器打开 http://127.0.0.1:8000

界面与正式 agent_chat 使用同一条模型调用、确定性路由和自验链路。
未设置 LOOMQ_LLM_* 时会明确提示配置，不伪造 Agent 回答。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from ..llm_client import REQUIRED_ENV
    from .backends import capability_basis, load_backends
    from .core import agent_chat
    from .presenter import diagram, explain as explain_circuit
    from .verifier import extract_qasm, verify
except ImportError:  # 直接以脚本方式运行时。
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from starter_kit.agent.backends import capability_basis, load_backends
    from starter_kit.agent.core import agent_chat
    from starter_kit.agent.presenter import diagram, explain as explain_circuit
    from starter_kit.agent.verifier import extract_qasm, verify
    from starter_kit.llm_client import REQUIRED_ENV


UI_PATH = Path(__file__).resolve().parent / "ui.html"

def handle_build(prompt: str) -> dict:
    if not all(os.environ.get(name) for name in REQUIRED_ENV):
        return {
            "kind": "error",
            "ok": False,
            "message": "请先配置 LOOMQ_LLM_BASE_URL、LOOMQ_LLM_API_KEY 和 LOOMQ_LLM_MODEL。",
        }

    reply = agent_chat(prompt)
    qasm = extract_qasm(reply)
    if qasm:
        result = verify(qasm)
        if not result.ok or result.circuit is None:
            return {
                "kind": "circuit",
                "ok": False,
                "message": result.message,
                "qasm": qasm,
            }
        return {
            "kind": "circuit",
            "ok": True,
            "intent": f"量子电路，{result.circuit.qubit_count} 比特",
            "qasm": qasm,
            "circuit": asdict(result.circuit),
            "diagram": diagram(result.circuit),
            "probabilities": result.probabilities,
            "explain": explain_circuit(result.circuit, result.probabilities),
            "fidelity": result.fidelity,
        }
    for backend in load_backends():
        if backend["id"] in reply:
            return {
                "kind": "backend",
                "ok": True,
                "backend": backend["id"],
                "explain": reply,
                "basis": capability_basis(),
            }
    return {"kind": "explanation", "ok": True, "explain": reply}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path != "/api/build":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = handle_build(str(payload.get("prompt", "")))
        except Exception as exc:  # UI 永远不该看到 500。
            result = {"kind": "circuit", "ok": False, "message": f"处理失败：{exc}"}
        self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="LoomQ L2 本地 UI 服务")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{server.server_port}"
    mode = (
        "真实模型"
        if all(os.environ.get(name) for name in REQUIRED_ENV)
        else "未配置模型（请求时会提示设置 LOOMQ_LLM_*）"
    )
    print(f"LoomQ 量子助手已启动：{url}\n意图解析模式：{mode}\n按 Ctrl+C 停止。")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
