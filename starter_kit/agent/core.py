"""Official L2 entry point: model call, deterministic routing, and QASM self-check."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

try:
    from ..llm_client import chat_completion
    from .backends import Constraints, explain as explain_backend, load_backends, select
    from .prompts import SYSTEM_PROMPT, retry_prompt
    from .verifier import VerificationResult, extract_qasm, verify
except ImportError:  # Support running modules directly from starter_kit/.
    from llm_client import chat_completion
    from agent.backends import Constraints, explain as explain_backend, load_backends, select
    from agent.prompts import SYSTEM_PROMPT, retry_prompt
    from agent.verifier import VerificationResult, extract_qasm, verify


MAX_ATTEMPTS = 3
CASE_BUDGET_SECONDS = 115.0
CALL_TIMEOUT_SECONDS = 35.0


@dataclass(frozen=True)
class AgentResult:
    text: str
    plan: dict[str, Any] | None = None
    verification: VerificationResult | None = None


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


def _probabilities(value: Any, qubit_count: int | None) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        return None
    result: dict[str, float] = {}
    for key, raw_probability in value.items():
        if (
            not isinstance(key, str)
            or not key
            or set(key) - {"0", "1"}
            or (qubit_count is not None and len(key) != qubit_count)
            or isinstance(raw_probability, bool)
        ):
            return None
        try:
            probability = float(raw_probability)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(probability) or probability < 0:
            return None
        result[key] = probability
    total = sum(result.values())
    return ({key: value / total for key, value in result.items()} if total > 0 else None)


def _small_distribution_qasm(probabilities: dict[str, float] | None) -> str | None:
    """Prepare a small exact distribution with the supported gate set."""

    outcomes = [(bits, probability) for bits, probability in (probabilities or {}).items() if probability > 1e-12]
    if not outcomes or len({len(bits) for bits, _ in outcomes}) != 1:
        return None
    qubits = len(outcomes[0][0])
    if all(math.isclose(probability, outcomes[0][1], abs_tol=1e-12) for _, probability in outcomes):
        values = {int(bits, 2) for bits, _ in outcomes}
        base = min(values)
        rows = [value ^ base for value in sorted(values) if value != base]
        rank = 0
        pivots: list[int] = []
        for bit in reversed(range(qubits)):
            pivot = next(
                (index for index in range(rank, len(rows)) if rows[index] & (1 << bit)),
                None,
            )
            if pivot is None:
                continue
            rows[rank], rows[pivot] = rows[pivot], rows[rank]
            for index in range(len(rows)):
                if index != rank and rows[index] & (1 << bit):
                    rows[index] ^= rows[rank]
            pivots.append(bit)
            rank += 1
        rows = rows[:rank]
        span = {0}
        for row in rows:
            span |= {value ^ row for value in tuple(span)}
        if len(values) == 1 << rank and {base ^ value for value in span} == values:
            for row, pivot in zip(rows, pivots):
                if base & (1 << pivot):
                    base ^= row
            lines = [
                "OPENQASM 2.0;",
                'include "qelib1.inc";',
                f"qreg q[{qubits}];",
                f"creg c[{qubits}];",
            ]
            lines.extend(f"x q[{bit}];" for bit in range(qubits) if base & (1 << bit))
            for row, pivot in zip(rows, pivots):
                lines.append(f"h q[{pivot}];")
                lines.extend(
                    f"cx q[{pivot}], q[{bit}];"
                    for bit in range(qubits)
                    if bit != pivot and row & (1 << bit)
                )
            lines.append("measure q -> c;")
            return "\n".join(lines)

    if len(outcomes) != 2:
        return None
    lines = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"qreg q[{qubits}];",
        f"creg c[{qubits}];",
    ]
    (base, _), (other, other_probability) = outcomes
    differences = [index for index in range(qubits) if base[index] != other[index]]
    if not differences:
        return None
    pivot = differences[0]
    if base[pivot] == "1":
        (base, _), (other, other_probability) = outcomes[1], outcomes[0]
    lines.extend(f"x q[{qubits - 1 - index}];" for index, bit in enumerate(base) if bit == "1")
    pivot_qubit = qubits - 1 - pivot
    angle = 2 * math.asin(math.sqrt(other_probability))
    lines.append(f"ry({angle:.15g}) q[{pivot_qubit}];")
    lines.extend(
        f"cx q[{pivot_qubit}], q[{qubits - 1 - index}];"
        for index in differences
        if index != pivot
    )
    lines.append("measure q -> c;")
    return "\n".join(lines)


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


def _qasm_answer(qasm: str, message: str, answer: str | None = None) -> str:
    prefix = f"{answer.strip()}\n\n" if answer and answer.strip() else ""
    return f"{prefix}{message}\n\n```qasm\n{qasm.strip()}\n```"


def agent_result(prompt: str) -> AgentResult:
    """Return the validated text plus metadata used by the local product UI."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    started = time.monotonic()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    last_content = ""
    last_qasm: str | None = None
    last_plan: dict[str, Any] | None = None
    last_verification: VerificationResult | None = None
    locked_probabilities: dict[str, float] | None = None

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
        last_plan = plan
        task = str(plan.get("task", "")).strip().lower()

        if "backend" in task:
            return AgentResult(_backend_answer(plan), plan)

        answer = plan.get("answer")
        if task == "explain" and isinstance(answer, str) and answer.strip():
            return AgentResult(answer.strip(), plan)

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
            qubit_count = _integer(plan.get("num_qubits"))
            proposed_probabilities = _probabilities(
                plan.get("expected_probabilities"), qubit_count
            )
            if locked_probabilities is None and proposed_probabilities:
                locked_probabilities = proposed_probabilities
            result = verify(
                qasm,
                target,
                qubit_count,
                locked_probabilities,
            )
            last_verification = result
            if result.ok:
                compound_answer = (
                    answer if isinstance(answer, str) and answer.strip() else None
                )
                return AgentResult(
                    _qasm_answer(qasm, result.message, compound_answer),
                    plan,
                    result,
                )
            feedback = result.feedback
        elif _known_backend_reply(last_content):
            # A model that answered directly still satisfies the required model
            # call and provides a canonical machine-readable backend ID.
            return AgentResult(last_content, plan)
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
        fallback_qasm = _small_distribution_qasm(locked_probabilities)
        if fallback_qasm:
            fallback_result = verify(
                fallback_qasm,
                "custom",
                len(next(iter(locked_probabilities))),
                locked_probabilities,
            )
            if fallback_result.ok:
                answer = last_plan.get("answer") if last_plan else None
                return AgentResult(
                    _qasm_answer(
                        fallback_qasm,
                        "模型候选未通过目标校验；LoomQ 已根据确认的目标分布完成本地合成。",
                        answer if isinstance(answer, str) else None,
                    ),
                    last_plan,
                    fallback_result,
                )
        return AgentResult(
            "模型已返回候选电路，但目标语义自检未通过。",
            last_plan,
            last_verification,
        )
    return AgentResult(last_content or "模型没有返回可用结果。", last_plan)


def agent_chat(prompt: str) -> str:
    """Official L2 string interface backed by the structured internal result."""

    return agent_result(prompt).text
