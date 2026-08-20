"""Official L2 entry point: model call, deterministic routing, and QASM self-check."""

from __future__ import annotations

import json
import time
from typing import Any

try:
    from ..llm_client import chat_completion
    from .backends import Constraints, explain as explain_backend, load_backends, select
    from .prompts import SYSTEM_PROMPT, retry_prompt
    from .verifier import extract_qasm, verify
except ImportError:  # Support running modules directly from starter_kit/.
    from llm_client import chat_completion
    from agent.backends import Constraints, explain as explain_backend, load_backends, select
    from agent.prompts import SYSTEM_PROMPT, retry_prompt
    from agent.verifier import extract_qasm, verify


MAX_ATTEMPTS = 3
CASE_BUDGET_SECONDS = 115.0
CALL_TIMEOUT_SECONDS = 35.0


def _content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("模型响应缺少 choices[0].message.content") from exc
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        ).strip()
        if text:
            return text
    raise ValueError("模型响应 content 不是文本")


def _json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for position, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return False


def _backend_answer(plan: dict[str, Any]) -> str:
    raw = plan.get("constraints")
    raw = raw if isinstance(raw, dict) else {}
    constraints = Constraints(
        min_qubits=_integer(raw.get("min_qubits")),
        no_queue=_boolean(raw.get("no_queue")),
        free_only=_boolean(raw.get("free_only")),
        no_account=_boolean(raw.get("no_account")),
        prefer_hardware=_boolean(raw.get("prefer_hardware")),
    )
    results = select(constraints)
    # The scored reply contains one canonical ID; alternatives can make an
    # otherwise correct recommendation ambiguous to a machine parser.
    return explain_backend(constraints, results[:1])


def _known_backend_reply(text: str) -> bool:
    return any(backend["id"] in text for backend in load_backends())


def _qasm_answer(qasm: str, message: str) -> str:
    return f"{message}\n\n```qasm\n{qasm.strip()}\n```"


def agent_chat(prompt: str) -> str:
    """Call the configured model and return a deterministically validated answer."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    started = time.monotonic()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    last_content = ""
    last_qasm: str | None = None

    for _ in range(MAX_ATTEMPTS):
        remaining = CASE_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 5:
            break
        try:
            response = chat_completion(
                messages,
                request_timeout=min(CALL_TIMEOUT_SECONDS, remaining - 5),
            )
        except RuntimeError:
            if last_content:
                break
            raise
        last_content = _content(response)
        plan = _json_object(last_content) or {}
        task = str(plan.get("task", "")).strip().lower()

        if "backend" in task:
            return _backend_answer(plan)

        answer = plan.get("answer")
        if task == "explain" and isinstance(answer, str) and answer.strip():
            return answer.strip()

        qasm_value = plan.get("qasm")
        qasm = (
            extract_qasm(qasm_value) or qasm_value.strip()
            if isinstance(qasm_value, str)
            else None
        )
        qasm = qasm or extract_qasm(last_content)
        if qasm:
            last_qasm = qasm
            target = plan.get("target_state")
            target = target if isinstance(target, str) else None
            result = verify(qasm, target, _integer(plan.get("num_qubits")))
            if result.ok:
                return _qasm_answer(qasm, result.message)
            feedback = result.feedback
        elif _known_backend_reply(last_content):
            # A model that answered directly still satisfies the required model
            # call and provides a canonical machine-readable backend ID.
            return last_content
        else:
            feedback = (
                "No complete QASM or backend constraints were found. Follow the "
                "requested JSON schema exactly."
            )

        messages.extend(
            (
                {"role": "assistant", "content": last_content},
                {"role": "user", "content": retry_prompt(feedback)},
            )
        )

    if last_qasm:
        return _qasm_answer(last_qasm, "模型已返回候选电路，但自检未通过。")
    return last_content or "模型没有返回可用结果。"
