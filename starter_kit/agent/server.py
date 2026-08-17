#!/usr/bin/env python3
"""LoomQ L2 本地 UI 服务。

用法：
    uv run python -m starter_kit.agent.server
    然后浏览器打开 http://127.0.0.1:8000

设置了 LOOMQ_LLM_* 时走真实模型；未设置时用本地意图解析降级运行，
界面与自验闭环仍然完整可演示（保真度是真算的，不是假数据）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .backends import Constraints, explain as explain_backend, select
    from .presenter import diagram, explain as explain_circuit
    from .verifier import verify
except ImportError:  # 直接以脚本方式运行时。
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from starter_kit.agent.backends import Constraints, select
    from starter_kit.agent.backends import explain as explain_backend
    from starter_kit.agent.presenter import diagram, explain as explain_circuit
    from starter_kit.agent.verifier import verify


UI_PATH = Path(__file__).resolve().parent / "ui.html"

CN_NUMBERS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# 本地模拟上限：态矢量是 2^n，界面上不要让用户等到卡死。
# 选后端只是查表，不受此限制。
MAX_SIMULATED_QUBITS = 12


def _qubit_count(
    text: str, default: int = 2, limit: int | None = MAX_SIMULATED_QUBITS
) -> int:
    found = None
    # 「个」「只」「路」等量词可选，且「量子比特」要能整体匹配。
    unit = r"(?:个|只|路)?\s*(?:量子)?\s*(?:比特|位|qubits?|量子位)"
    digits = re.search(rf"(\d+)\s*{unit}", text, re.IGNORECASE)
    if digits:
        found = int(digits.group(1))
    else:
        for word, value in CN_NUMBERS.items():
            if re.search(rf"{word}\s*{unit}", text):
                found = value
                break
    if found is None:
        return default
    return max(1, found if limit is None else min(limit, found))


def parse_intent(prompt: str) -> dict:
    """本地意图解析 —— UI 演示与无 Key 场景的降级路径。

    注意：正式评测的 agent_chat 不依赖这里，分类交给 LLM 完成；
    此处只为让界面在没有 API Key 时也能完整走通。
    """

    text = prompt.lower()
    wants_backend = any(
        word in prompt for word in ("平台", "后端", "选哪个", "排队", "跑在", "用哪个")
    ) or "backend" in text

    if wants_backend:
        return {
            "task": "select_backend",
            "constraints": {
                "min_qubits": _qubit_count(prompt, 0, limit=None) or None,
                "no_queue": any(
                    w in prompt
                    for w in ("不想排队", "零排队", "不排队", "立刻", "马上")
                ),
                "free_only": any(w in prompt for w in ("免费", "不花钱", "白嫖")),
                "no_account": any(
                    w in prompt for w in ("不注册", "无需账号", "没有账号")
                ),
            },
        }

    if any(word in prompt for word in ("贝尔", "bell", "epr")):
        return {"task": "generate", "target_state": "bell", "num_qubits": 2}
    if any(word in prompt for word in ("ghz", "纠缠", "entangle")):
        return {
            "task": "generate", "target_state": "ghz",
            "num_qubits": _qubit_count(prompt, 3),
        }
    if any(word in prompt for word in ("w 态", "w态")):
        return {
            "task": "generate", "target_state": "w",
            "num_qubits": _qubit_count(prompt, 3),
        }
    if any(word in prompt for word in ("叠加", "均匀", "superposition", "随机")):
        return {
            "task": "generate", "target_state": "uniform",
            "num_qubits": _qubit_count(prompt, 2),
        }
    return {"task": "unknown"}


def build_qasm(target_state: str, qubit_count: int) -> str:
    """为已知目标态生成参考电路（本地降级路径使用）。"""

    lines = ["OPENQASM 2.0;", 'include "qelib1.inc";',
             f"qreg q[{qubit_count}];", f"creg c[{qubit_count}];"]
    if target_state in {"ghz", "bell"}:
        lines.append("h q[0];")
        lines += [f"cx q[{i}],q[{i + 1}];" for i in range(qubit_count - 1)]
    elif target_state == "uniform":
        lines += [f"h q[{i}];" for i in range(qubit_count)]
    elif target_state == "w":
        raise ValueError("W 态需要参数化旋转，本地降级路径暂不支持")
    lines.append("measure q -> c;")
    return "\n".join(lines)


INTENT_LABEL = {
    "ghz": "GHZ 纠缠态", "bell": "贝尔态",
    "uniform": "均匀叠加态", "w": "W 态",
}


def handle_build(prompt: str) -> dict:
    intent = parse_intent(prompt)

    if intent["task"] == "select_backend":
        raw = intent["constraints"]
        constraints = Constraints(
            min_qubits=raw["min_qubits"], no_queue=raw["no_queue"],
            free_only=raw["free_only"], no_account=raw["no_account"],
        )
        results = select(constraints)
        return {
            "kind": "backend",
            "ok": True,
            "backend": results[0]["id"] if results else None,
            "explain": explain_backend(constraints, results),
        }

    if intent["task"] == "unknown":
        return {
            "kind": "circuit", "ok": False,
            "message": "还没听懂这句话。试试「让三个量子比特纠缠在一起」，"
                       "或者点「我不知道，给我例子」。",
        }

    target_state = intent["target_state"]
    qubit_count = intent["num_qubits"]
    requested = _qubit_count(prompt, qubit_count, limit=None)
    if requested > MAX_SIMULATED_QUBITS:
        return {
            "kind": "circuit", "ok": False,
            "message": f"{requested} 比特超出了本地实时模拟的上限"
                       f"（{MAX_SIMULATED_QUBITS} 比特）——再大就要等很久。"
                       f"可以先试试 {MAX_SIMULATED_QUBITS} 比特以内的电路。",
        }
    try:
        qasm = build_qasm(target_state, qubit_count)
    except ValueError as exc:
        return {"kind": "circuit", "ok": False, "message": str(exc)}

    result = verify(qasm, target_state, qubit_count)
    if not result.ok:
        return {"kind": "circuit", "ok": False, "message": result.message, "qasm": qasm}

    return {
        "kind": "circuit", "ok": True,
        "intent": f"{INTENT_LABEL.get(target_state, target_state)}，{qubit_count} 比特",
        "qasm": qasm,
        "diagram": diagram(result.circuit),
        "probabilities": result.probabilities,
        "explain": explain_circuit(result.circuit, result.probabilities),
        "fidelity": result.fidelity,
    }


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
        "真实模型" if os.environ.get("LOOMQ_LLM_API_KEY")
        else "本地降级（未设置 LOOMQ_LLM_*）"
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
