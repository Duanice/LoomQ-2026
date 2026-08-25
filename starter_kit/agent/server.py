#!/usr/bin/env python3
"""LoomQ L2 本地 UI 服务。

用法：
    uv run python -m starter_kit.agent.server
    欢迎页：http://127.0.0.1:8000/
    Builder：http://127.0.0.1:8000/builder

界面与正式 agent_chat 使用同一条模型调用、确定性路由和自验链路。
未设置 LOOMQ_LLM_* 时仍可阅读教程并运行内置的离线 Bell 示例；只有用户
真正请求 Agent 时才提示配置，离线示例不会伪装成模型回答。
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
    from .verifier import circuits_are_distinct, extract_qasm, verify
except ImportError:  # 直接运行脚本，或 starter_kit 被提取为评测根目录时。
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.backends import capability_basis, load_backends
    from agent.core import AgentResult, agent_result
    from agent.explainer import (
        decompose_request,
        explain_circuit,
        explain_question,
        explanation_text,
    )
    from agent.presenter import diagram
    from agent.verifier import circuits_are_distinct, extract_qasm, verify
    from llm_client import REQUIRED_ENV


UI_PATH = Path(__file__).resolve().parent / "ui.html"
OFFLINE_DEMO_PATH = UI_PATH.parent.parent / "circuits" / "bell.qasm"


def _offline_explanation(language: str) -> dict:
    """Plain-language copy for the explicitly labelled, fixed offline demo."""

    if language == "en":
        return {
            "overview": (
                "This built-in example creates a Bell pair: the two qubits are "
                "measured as 00 or 11 with equal probability."
            ),
            "steps": [
                {
                    "operation_id": "op_0",
                    "plain": "Put the first qubit into a 50/50 superposition.",
                    "purpose": "It supplies the two possible branches, 0 and 1.",
                    "terms": [
                        {"symbol": "H", "meaning": "a gate that creates superposition"},
                        {"symbol": "q[0]", "meaning": "the first qubit"},
                    ],
                },
                {
                    "operation_id": "op_1",
                    "plain": "Make the second qubit follow the first one.",
                    "purpose": "This correlates the two read-outs into 00 and 11.",
                    "terms": [
                        {"symbol": "CNOT", "meaning": "a controlled flip"},
                        {"symbol": "q[1]", "meaning": "the second qubit"},
                    ],
                },
            ],
            "measurement": "Read both qubits: only 00 and 11 should appear, about half each.",
            "result": "The local simulator verified the Bell state with fidelity 1.00.",
        }
    return {
        "overview": "这个内置示例制备贝尔态：两个量子比特只会一起测得 00 或 11，各约一半。",
        "steps": [
            {
                "operation_id": "op_0",
                "plain": "先让第 1 个量子比特同时保留 0 和 1 两种可能。",
                "purpose": "为后续的 00、11 两条结果分支做准备。",
                "terms": [
                    {"symbol": "H", "meaning": "制造叠加的量子门"},
                    {"symbol": "q[0]", "meaning": "程序里的第 1 个量子比特"},
                ],
            },
            {
                "operation_id": "op_1",
                "plain": "再让第 2 个量子比特跟随第 1 个一起变化。",
                "purpose": "把两次读数关联成 00 和 11，而不是各自随机。",
                "terms": [
                    {"symbol": "CNOT", "meaning": "由前一个比特控制的翻转门"},
                    {"symbol": "q[1]", "meaning": "程序里的第 2 个量子比特"},
                ],
            },
        ],
        "measurement": "最后同时读取两个比特，应只看到 00 和 11，各约 50%。",
        "result": "本地模拟器已验证该电路的贝尔态保真度为 1.00。",
    }


def offline_demo(language: str = "zh") -> dict:
    """Run the bundled Bell circuit locally without calling the LLM."""

    qasm = OFFLINE_DEMO_PATH.read_text(encoding="utf-8")
    result = verify(qasm, target_state="bell", qubit_count=2)
    if not result.ok or result.circuit is None:
        raise RuntimeError(f"内置 Bell 示例未通过本地验证：{result.message}")

    request = (
        "Run the built-in offline Bell-state example"
        if language == "en"
        else "运行内置的离线 Bell 态示例"
    )
    label = "Offline Bell demo" if language == "en" else "离线 Bell 示例"
    task = {"id": "task_1", "kind": "circuit_build", "request": request}
    explanation = _offline_explanation(language)
    payload = {
        "kind": "circuit",
        "ok": True,
        "offline_demo": True,
        "source": "starter_kit/circuits/bell.qasm",
        "intent": {
            "primary_goal": "circuit_build",
            "goals": ["circuit_build"],
            "label": label,
        },
        "tasks": [task],
        "relationships": [],
        "qasm": qasm,
        "circuit": asdict(result.circuit),
        "diagram": diagram(result.circuit),
        "probabilities": result.probabilities,
        "expected_probabilities": {"00": 0.5, "11": 0.5},
        "validation_stage": result.stage,
        "explanation": explanation,
        "concept": None,
        "explain": explanation_text(explanation),
        "fidelity": result.fidelity,
        "all_tasks_ok": True,
    }
    payload["circuits"] = [_circuit_item(payload, task)]
    return payload


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
        "relationships": task_plan["relationships"] if task_plan else None,
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
        for task in circuit_tasks[1:]:
            circuits.append(
                _run_circuit_task(
                    task,
                    language,
                    original_prompt=prompt,
                    relationships=task_plan.get("relationships", []),
                    completed_circuits=circuits,
                )
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


def _execution_prompt(
    task: dict,
    original_prompt: str,
    relationships: list[dict],
    completed_circuits: list[dict],
    feedback: str | None = None,
) -> str:
    relevant = [
        relationship
        for relationship in relationships
        if task["id"] in relationship["task_ids"]
    ]
    if not relevant:
        return task["request"]
    envelope = {
        "original_request": original_prompt,
        "current_task": task,
        "relationships": relevant,
        "completed_circuits": [
            {
                "task_id": item["task_id"],
                "request": item["request"],
                "qasm": item.get("qasm"),
                "probabilities": item.get("probabilities"),
            }
            for item in completed_circuits
            if item.get("ok")
            and any(item["task_id"] in relation["task_ids"] for relation in relevant)
        ],
    }
    if feedback:
        envelope["validation_feedback"] = feedback
    return (
        "Execute exactly the current task in this validated LoomQ task envelope. "
        "Return the normal planning JSON required by the system prompt.\n\n"
        + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    )


def _relationship_violation(
    task: dict,
    candidate_circuit,
    relationships: list[dict],
    completed_circuits: list[dict],
) -> str | None:
    for relationship in relationships:
        if task["id"] not in relationship["task_ids"]:
            continue
        for completed in completed_circuits:
            if (
                not completed.get("ok")
                or completed["task_id"] not in relationship["task_ids"]
            ):
                continue
            completed_result = verify(completed["qasm"])
            if (
                completed_result.circuit is not None
                and not circuits_are_distinct(
                    completed_result.circuit,
                    candidate_circuit,
                    relationship["basis"],
                )
            ):
                return (
                    f"候选电路与任务 {completed['task_id']} 在 "
                    f"{relationship['basis']} 层面相同，未满足已验证的 "
                    "pairwise_distinct 关系。请生成真正满足当前任务且不同的电路。"
                )
    return None


def _run_circuit_task(
    task: dict,
    language: str = "zh",
    *,
    original_prompt: str = "",
    relationships: list[dict] | None = None,
    completed_circuits: list[dict] | None = None,
) -> dict:
    relationships = relationships or []
    completed_circuits = completed_circuits or []
    feedback = None
    try:
        for _ in range(2):
            response = agent_result(
                _execution_prompt(
                    task,
                    original_prompt,
                    relationships,
                    completed_circuits,
                    feedback,
                )
            )
            qasm = extract_qasm(response.text)
            if not qasm:
                feedback = (
                    response.verification.feedback
                    if response.verification is not None
                    else "模型没有返回完整的 OpenQASM 2.0 电路。"
                )
                continue
            result = response.verification or verify(qasm)
            if not result.ok or result.circuit is None:
                feedback = result.feedback
                continue
            feedback = _relationship_violation(
                task,
                result.circuit,
                relationships,
                completed_circuits,
            )
            if feedback:
                continue
            payload = _circuit_response(
                task["request"],
                response,
                qasm,
                task_plan={
                    "label": task["request"],
                    "tasks": [task],
                    "relationships": [],
                },
                expand_tasks=False,
                language=language,
            )
            return _circuit_item(payload, task)
        return {
            "task_id": task["id"],
            "request": task["request"],
            "ok": False,
            "message": feedback or "电路任务未能通过验证。",
        }
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
        task_plan = _decompose(prompt, language)
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
    task_plan = _decompose(prompt, language)
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
        if self.path not in {"/api/build", "/api/demo"}:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            language = "en" if str(payload.get("language", "zh")) == "en" else "zh"
            result = (
                offline_demo(language)
                if self.path == "/api/demo"
                else handle_build(str(payload.get("prompt", "")), language)
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
        else "离线体验（教程和 Bell Demo 可用；Agent 请求时才提示 LOOMQ_LLM_*）"
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
