"""Prompt-skill-driven beginner explanations for validated Circuit IR."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

try:
    from ..llm_client import chat_completion
    from ..qasm_parser import Circuit
    from .core import _content, _json_object
except ImportError:  # Support direct execution from starter_kit/.
    from llm_client import chat_completion
    from qasm_parser import Circuit
    from agent.core import _content, _json_object


SKILL_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "explain-quantum-circuit"
    / "SKILL.md"
)
SKILL_PROMPT = SKILL_PATH.read_text(encoding="utf-8")
QUESTION_SKILL_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "explain-user-question"
    / "SKILL.md"
)
QUESTION_SKILL_PROMPT = QUESTION_SKILL_PATH.read_text(encoding="utf-8")
DECOMPOSE_SKILL_PATH = (
    Path(__file__).resolve().parent
    / "skills"
    / "decompose-user-request"
    / "SKILL.md"
)
DECOMPOSE_SKILL_PROMPT = DECOMPOSE_SKILL_PATH.read_text(encoding="utf-8")

# 界面语言开关：技能提示词本身以简体中文为准，需要英文时追加一条覆盖指令。
# 只影响 UI 的解释文案，不参与 agent_chat 的客观评分路径。
LANGUAGE_DIRECTIVE = {
    "en": (
        "\n\nLANGUAGE OVERRIDE: ignore every instruction above that asks for "
        "Simplified Chinese. Write all human-readable string values in natural "
        "English instead. Keep every JSON key, enum value, identifier, gate name "
        "and operation_id exactly as specified — translate only prose."
    ),
}
MAX_ATTEMPTS = 2
CALL_TIMEOUT_SECONDS = 30.0


def _input(
    user_goal: str,
    circuit: Circuit,
    probabilities: dict[str, float],
    fidelity: float | None,
) -> dict[str, Any]:
    return {
        "user_goal": user_goal,
        "qubit_count": circuit.qubit_count,
        "cbit_count": circuit.cbit_count,
        "operations": [
            {
                "operation_id": f"op_{index}",
                "name": operation.name,
                "qubits": list(operation.qubits),
                "parameter": operation.parameter,
            }
            for index, operation in enumerate(circuit.operations)
        ],
        "measurements": [
            {"qubit": measurement.qubit, "cbit": measurement.cbit}
            for measurement in circuit.measurements
        ],
        "bit_order": "little",
        "verified_result": {
            "probabilities": probabilities,
            "fidelity": fidelity,
        },
    }


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} fields do not match the output contract")
    return value


# 同样的意思，英文所需字符数约为中文的 2~2.5 倍；长度上限按语言缩放，
# 否则英文解释会稳定超限并退化成兜底文案。
_LENGTH_SCALE = {"en": 2.5}
_ACTIVE_SCALE = 1.0


def _scaled(limit: int) -> int:
    return int(limit * _ACTIVE_SCALE)


def _text(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    allowed = _scaled(limit)
    if len(value) > allowed:
        raise ValueError(f"{label} exceeds {allowed} characters")
    return value


def _validate_circuit(value: Any, operation_count: int) -> dict[str, Any]:
    result = _object(
        value,
        {"overview", "steps", "measurement", "result"},
        "root",
    )
    overview = _text(result["overview"], 120, "overview")
    raw_steps = result["steps"]
    if not isinstance(raw_steps, list) or len(raw_steps) != operation_count:
        raise ValueError("steps must match the validated operation count")

    steps = []
    for index, raw_step in enumerate(raw_steps):
        step = _object(
            raw_step,
            {"operation_id", "plain", "purpose", "terms"},
            f"steps[{index}]",
        )
        expected_id = f"op_{index}"
        if step["operation_id"] != expected_id:
            raise ValueError(f"steps[{index}].operation_id must be {expected_id}")
        raw_terms = step["terms"]
        if not isinstance(raw_terms, list) or len(raw_terms) > 6:
            raise ValueError(f"steps[{index}].terms must contain at most 6 items")
        terms = []
        for term_index, raw_term in enumerate(raw_terms):
            term = _object(
                raw_term,
                {"symbol", "meaning"},
                f"steps[{index}].terms[{term_index}]",
            )
            terms.append(
                {
                    "symbol": _text(term["symbol"], 30, "term symbol"),
                    "meaning": _text(term["meaning"], 60, "term meaning"),
                }
            )
        steps.append(
            {
                "operation_id": expected_id,
                "plain": _text(step["plain"], 100, f"steps[{index}].plain"),
                "purpose": _text(
                    step["purpose"], 80, f"steps[{index}].purpose"
                ),
                "terms": terms,
            }
        )

    raw_result = result["result"]
    if raw_result is not None:
        raw_result = _text(raw_result, 150, "result")
    return {
        "overview": overview,
        "steps": steps,
        "measurement": _text(result["measurement"], 150, "measurement"),
        "result": raw_result,
    }


def _validate_lesson(value: Any) -> dict[str, Any]:
    lesson = _object(
        value,
        {"intent", "title", "summary", "visual", "cards", "takeaway"},
        "root",
    )
    intent = _object(
        lesson["intent"], {"primary_goal", "goals", "label"}, "intent"
    )
    primary_goal = intent["primary_goal"]
    allowed_goals = {
        "quantum_concept",
        "product_help",
        "usage_help",
        "competition_help",
        "general_question",
    }
    goals = intent["goals"]
    if not isinstance(primary_goal, str) or primary_goal not in allowed_goals:
        raise ValueError("intent.primary_goal is not allowed")
    if (
        not isinstance(goals, list)
        or goals != [primary_goal]
    ):
        raise ValueError("intent.goals must contain only the standalone lesson goal")
    visual = _object(
        lesson["visual"], {"type", "caption", "items", "link"}, "visual"
    )
    visual_type = visual["type"]
    if visual_type not in {"flow", "compare", "relation"}:
        raise ValueError("visual.type must be flow, compare, or relation")
    raw_items = visual["items"]
    expected_range = range(2, 6) if visual_type == "flow" else range(2, 3)
    if not isinstance(raw_items, list) or len(raw_items) not in expected_range:
        raise ValueError("visual.items count does not match visual.type")
    items = []
    for index, raw_item in enumerate(raw_items):
        item = _object(raw_item, {"label", "detail"}, f"visual.items[{index}]")
        items.append(
            {
                "label": _text(item["label"], 24, "visual item label"),
                "detail": _text(item["detail"], 50, "visual item detail"),
            }
        )
    if visual_type == "relation":
        link = _text(visual["link"], 40, "visual link")
    elif visual["link"] is None:
        link = None
    else:
        raise ValueError("visual.link must be null unless type is relation")

    raw_cards = lesson["cards"]
    kinds = {"definition", "analogy", "example", "fact", "caution", "action"}
    if not isinstance(raw_cards, list) or not 2 <= len(raw_cards) <= 3:
        raise ValueError("cards must contain 2 to 3 items")
    cards = []
    for index, raw_card in enumerate(raw_cards):
        card = _object(raw_card, {"kind", "title", "body"}, f"cards[{index}]")
        kind = card["kind"]
        if kind not in kinds:
            raise ValueError(f"cards[{index}].kind is not allowed")
        cards.append(
            {
                "kind": kind,
                "title": _text(card["title"], 20, "card title"),
                "body": _text(card["body"], 100, "card body"),
            }
        )
    return {
        "intent": {
            "primary_goal": primary_goal,
            "goals": goals,
            "label": _text(intent["label"], 48, "intent label"),
        },
        "title": _text(lesson["title"], 40, "title"),
        "summary": _text(lesson["summary"], 80, "summary"),
        "visual": {
            "type": visual_type,
            "caption": _text(visual["caption"], 80, "visual caption"),
            "items": items,
            "link": link,
        },
        "cards": cards,
        "takeaway": _text(lesson["takeaway"], 50, "takeaway"),
    }


def _validate_decomposition(value: Any) -> dict[str, Any]:
    result = _object(value, {"label", "tasks", "relationships"}, "root")
    raw_tasks = result["tasks"]
    allowed = {
        "quantum_concept",
        "product_help",
        "usage_help",
        "competition_help",
        "general_question",
        "circuit_build",
        "backend_select",
    }
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 3:
        raise ValueError("tasks must contain 1 to 3 items")
    tasks = []
    for index, raw_task in enumerate(raw_tasks, 1):
        task = _object(raw_task, {"id", "kind", "request"}, f"tasks[{index - 1}]")
        expected_id = f"task_{index}"
        if task["id"] != expected_id or task["kind"] not in allowed:
            raise ValueError(f"tasks[{index - 1}] id or kind is invalid")
        tasks.append(
            {
                "id": expected_id,
                "kind": task["kind"],
                "request": _text(task["request"], 180, "task request"),
            }
        )
    task_kinds = {task["id"]: task["kind"] for task in tasks}
    raw_relationships = result["relationships"]
    if not isinstance(raw_relationships, list) or len(raw_relationships) > 3:
        raise ValueError("relationships must contain 0 to 3 items")
    relationships = []
    seen = set()
    for index, raw_relationship in enumerate(raw_relationships):
        relationship = _object(
            raw_relationship,
            {"type", "task_ids", "basis"},
            f"relationships[{index}]",
        )
        task_ids = relationship["task_ids"]
        if (
            relationship["type"] != "pairwise_distinct"
            or relationship["basis"] not in {
                "quantum_state",
                "measurement_distribution",
                "circuit_structure",
            }
            or not isinstance(task_ids, list)
            or not 2 <= len(task_ids) <= 3
            or len(set(task_ids)) != len(task_ids)
            or any(task_kinds.get(task_id) != "circuit_build" for task_id in task_ids)
        ):
            raise ValueError(f"relationships[{index}] is invalid")
        signature = (relationship["type"], tuple(task_ids), relationship["basis"])
        if signature in seen:
            raise ValueError(f"relationships[{index}] is duplicated")
        seen.add(signature)
        relationships.append(
            {
                "type": relationship["type"],
                "task_ids": task_ids,
                "basis": relationship["basis"],
            }
        )
    return {
        "label": _text(result["label"], 64, "label"),
        "tasks": tasks,
        "relationships": relationships,
    }


def _run_skill(
    skill_prompt: str,
    data: dict[str, Any],
    validate: Callable[[Any], dict[str, Any]],
    language: str = "zh",
) -> dict[str, Any]:
    global _ACTIVE_SCALE
    _ACTIVE_SCALE = _LENGTH_SCALE.get(language, 1.0)
    messages = [
        {"role": "system", "content": skill_prompt + LANGUAGE_DIRECTIVE.get(language, "")},
        {
            "role": "user",
            "content": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    last_error = "invalid structured explanation"
    for attempt in range(MAX_ATTEMPTS):
        response = chat_completion(messages, request_timeout=CALL_TIMEOUT_SECONDS)
        content = _content(response)
        try:
            return validate(_json_object(content))
        except ValueError as exc:
            last_error = str(exc)
            if attempt + 1 == MAX_ATTEMPTS:
                break
            messages.extend(
                (
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "The response failed deterministic validation: "
                            f"{last_error}. Return a corrected JSON object that "
                            "follows the skill output contract."
                        ),
                    },
                )
            )
    raise ValueError(f"structured explanation validation failed: {last_error}")


def explain_circuit(
    user_goal: str,
    circuit: Circuit,
    probabilities: dict[str, float],
    fidelity: float | None,
    language: str = "zh",
) -> dict[str, Any]:
    """Explain a trusted circuit through the application skill and validate it."""

    data = _input(user_goal, circuit, probabilities, fidelity)
    return _run_skill(
        SKILL_PROMPT,
        data,
        lambda value: _validate_circuit(value, len(circuit.operations)),
        language,
    )


def explain_question(
    question: str, draft_answer: str, language: str = "zh"
) -> dict[str, Any]:
    """Classify an explanatory request and create a validated visual lesson."""

    return _run_skill(
        QUESTION_SKILL_PROMPT,
        {"question": question, "draft_answer": draft_answer},
        _validate_lesson,
        language,
    )


def decompose_request(question: str, language: str = "zh") -> dict[str, Any]:
    """Lock independently satisfiable tasks before downstream rendering."""

    return _run_skill(
        DECOMPOSE_SKILL_PROMPT,
        {"question": question},
        _validate_decomposition,
        language,
    )


def explanation_text(explanation: dict[str, Any]) -> str:
    """Format validated structured text for the UI's full explanation tab."""

    lines = [explanation["overview"], "", "逐步说明："]
    for index, step in enumerate(explanation["steps"], 1):
        lines.extend(
            (
                f"第 {index} 步：{step['plain']}",
                f"为什么需要：{step['purpose']}",
            )
        )
    lines.extend(("", f"怎么读结果：{explanation['measurement']}"))
    if explanation["result"]:
        lines.append(f"本地验证结果：{explanation['result']}")
    return "\n".join(lines)
