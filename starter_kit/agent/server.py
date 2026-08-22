#!/usr/bin/env python3
"""LoomQ L2 本地 UI 服务。

用法：
    uv run python -m starter_kit.agent.server
    欢迎页：http://127.0.0.1:8000/
    Builder：http://127.0.0.1:8000/builder

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
    from .core import AgentResult, agent_result
    from .explainer import (
        decompose_request,
        explain_circuit,
        explain_question,
        explanation_text,
    )
    from .presenter import diagram
    from .verifier import extract_qasm, verify
except ImportError:  # 直接以脚本方式运行时。
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from starter_kit.agent.backends import capability_basis, load_backends
    from starter_kit.agent.core import AgentResult, agent_result
    from starter_kit.agent.explainer import (
        decompose_request,
        explain_circuit,
        explain_question,
        explanation_text,
    )
    from starter_kit.agent.presenter import diagram
    from starter_kit.agent.verifier import extract_qasm, verify
    from starter_kit.llm_client import REQUIRED_ENV


UI_PATH = Path(__file__).resolve().parent / "ui.html"


def _decompose(prompt: str, language: str = "zh") -> dict | None:
    try:
        return decompose_request(prompt, language)
    except (RuntimeError, ValueError) as exc:
        print(f"[LoomQ task decomposition fallback] {exc}")
        return None


def _knowledge_question(task_plan: dict | None) -> str | None:
    if not task_plan:
        return None
    requests = [
        task["request"]
        for task in task_plan["tasks"]
        if task["kind"] not in {"circuit_build", "backend_select"}
    ]
    return "\n".join(requests) if requests else None


def _intent(task_plan: dict | None, lesson: dict | None) -> dict | None:
    if task_plan:
        kinds = [task["kind"] for task in task_plan["tasks"]]
        return {
            "primary_goal": kinds[0],
            "goals": kinds,
            "label": task_plan["label"],
        }
    return lesson["intent"] if lesson else None


def _circuit_response(
    prompt: str,
    response: AgentResult,
    qasm: str,
    lesson: dict | None = None,
    task_plan: dict | None = None,
    expand_tasks: bool = True,
    language: str = "zh",
) -> dict:
    result = response.verification or verify(qasm)
    if not result.ok or result.circuit is None:
        return {
            "kind": "circuit",
            "ok": False,
            "message": result.message,
            "qasm": qasm,
        }
    try:
        explanation = explain_circuit(
            prompt,
            result.circuit,
            result.probabilities,
            result.fidelity,
            language,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"[LoomQ circuit explanation fallback] {exc}")
        explanation = None
    task_plan = task_plan or _decompose(prompt, language)
    knowledge_question = _knowledge_question(task_plan)
    if lesson is None and knowledge_question:
        try:
            answer = response.plan.get("answer") if response.plan else None
            lesson = explain_question(
                knowledge_question,
                answer if isinstance(answer, str) and answer.strip()
                else "请直接准确回答这个知识问题。",
                language,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"[LoomQ visual lesson fallback] {exc}")
    intent = _intent(task_plan, lesson) or {
        "primary_goal": "circuit_build",
        "goals": ["circuit_build"],
        "label": f"你想构建一个 {result.circuit.qubit_count} 比特量子电路",
    }
    payload = {
        "kind": "circuit",
        "ok": True,
        "intent": intent,
        "tasks": task_plan["tasks"] if task_plan else None,
        "qasm": qasm,
        "circuit": asdict(result.circuit),
        "diagram": diagram(result.circuit),
        "probabilities": result.probabilities,
        "expected_probabilities": result.expected_probabilities,
        "validation_stage": result.stage,
        "explanation": explanation,
        "concept": lesson,
        "explain": (
            explanation_text(explanation)
            if explanation
            else "小白解释暂时不可用；电路本身已通过本地验证。"
        ),
        "fidelity": result.fidelity,
    }
    circuit_tasks = [
        task
        for task in (task_plan or {}).get("tasks", [])
        if task["kind"] == "circuit_build"
    ]
    if not circuit_tasks:
        return payload

    first_task = circuit_tasks[0]
    circuits = [_circuit_item(payload, first_task)]
    if expand_tasks:
        circuits.extend(
            _run_circuit_task(task, language) for task in circuit_tasks[1:]
        )
    payload["circuits"] = circuits
    payload["all_tasks_ok"] = all(item["ok"] for item in circuits)
    return payload


def _circuit_item(payload: dict, task: dict) -> dict:
    fields = (
        "ok",
        "message",
        "qasm",
        "circuit",
        "diagram",
        "probabilities",
        "expected_probabilities",
        "validation_stage",
        "explanation",
        "explain",
        "fidelity",
    )
    return {
        "task_id": task["id"],
        "request": task["request"],
        **{field: payload[field] for field in fields if field in payload},
    }


def _run_circuit_task(task: dict, language: str = "zh") -> dict:
    try:
        response = agent_result(task["request"])
        qasm = extract_qasm(response.text)
        if not qasm:
            message = (
                response.verification.feedback
                if response.verification is not None
                else "模型没有返回完整的 OpenQASM 2.0 电路。"
            )
            return {
                "task_id": task["id"],
                "request": task["request"],
                "ok": False,
                "message": message,
            }
        payload = _circuit_response(
            task["request"],
            response,
            qasm,
            task_plan={"label": task["request"], "tasks": [task]},
            expand_tasks=False,
            language=language,
        )
        return _circuit_item(payload, task)
    except (RuntimeError, ValueError) as exc:
        return {
            "task_id": task["id"],
            "request": task["request"],
            "ok": False,
            "message": str(exc),
        }


def handle_build(prompt: str, language: str = "zh") -> dict:
    if not all(os.environ.get(name) for name in REQUIRED_ENV):
        return {
            "kind": "error",
            "ok": False,
            "message": "请先配置 LOOMQ_LLM_BASE_URL、LOOMQ_LLM_API_KEY 和 LOOMQ_LLM_MODEL。",
        }

    response = agent_result(prompt)
    reply = response.text
    qasm = extract_qasm(reply)
    if qasm:
        return _circuit_response(prompt, response, qasm, language=language)
    if response.verification is not None and not response.verification.ok:
        answer = response.plan.get("answer") if response.plan else None
        task_plan = _decompose(prompt)
        knowledge_question = _knowledge_question(task_plan)
        lesson = None
        if knowledge_question:
            try:
                lesson = explain_question(
                    knowledge_question,
                    answer if isinstance(answer, str) and answer.strip()
                    else "请直接准确回答这个知识问题。",
                    language,
                )
            except (RuntimeError, ValueError) as exc:
                print(f"[LoomQ partial explanation fallback] {exc}")
        return {
            "kind": "partial",
            "ok": False,
            "message": response.verification.feedback,
            "explain": answer,
            "intent": _intent(task_plan, lesson),
            "tasks": task_plan["tasks"] if task_plan else None,
            "concept": lesson,
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
    task_plan = _decompose(prompt)
    circuit_tasks = [
        task for task in (task_plan or {}).get("tasks", [])
        if task["kind"] == "circuit_build"
    ]
    if circuit_tasks:
        try:
            circuit_response = agent_result(
                "The validated intent includes circuit_build. Produce the complete "
                "OpenQASM 2.0 circuit requested below; do not omit the circuit even "
                f"when explanation is also requested.\n\nCircuit task:\n{circuit_tasks[0]['request']}"
            )
            circuit_qasm = extract_qasm(circuit_response.text)
        except RuntimeError as exc:
            print(f"[LoomQ circuit recovery fallback] {exc}")
            circuit_qasm = None
        if circuit_qasm:
            return _circuit_response(
                prompt,
                circuit_response,
                circuit_qasm,
                task_plan=task_plan,
                language=language,
            )
    knowledge_question = _knowledge_question(task_plan) or prompt
    try:
        lesson = explain_question(knowledge_question, reply, language)
    except (RuntimeError, ValueError) as exc:
        print(f"[LoomQ visual lesson fallback] {exc}")
        lesson = None
    return {
        "kind": "explanation",
        "ok": True,
        "explain": reply,
        "intent": _intent(task_plan, lesson),
        "tasks": task_plan["tasks"] if task_plan else None,
        "concept": lesson,
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
        path = self.path.partition("?")[0]
        if path in ("/", "/index.html", "/builder"):
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
            result = handle_build(
                str(payload.get("prompt", "")),
                "en" if str(payload.get("language", "zh")) == "en" else "zh",
            )
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
