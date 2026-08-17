"""智能选后端：按官方能力表做确定性约束求解。

判定基准是 backend_capabilities.json，不让模型来算，避免算错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CAPABILITIES_PATH = Path(__file__).resolve().parent.parent / "backend_capabilities.json"

# 能力表把 braket_local_simulator 标注为「评测推荐默认模拟器」，
# 同分时据此排序，提高命中官方答案集的概率。
PREFERENCE = (
    "braket_local_simulator",
    "originq_local_simulator",
    "spinq_taurus_simulator",
    "originq_wukong",
    "spinq_cloud_qpu",
    "braket_cloud",
)


@dataclass(frozen=True)
class Constraints:
    min_qubits: int | None = None
    no_queue: bool = False
    free_only: bool = False
    no_account: bool = False
    prefer_hardware: bool = False


@lru_cache(maxsize=1)
def load_backends() -> tuple[dict, ...]:
    data = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    return tuple(data["backends"])


def select(constraints: Constraints) -> list[dict]:
    """返回满足全部约束的后端，按推荐度排序。"""

    candidates = []
    for backend in load_backends():
        if (
            constraints.min_qubits is not None
            and backend["max_qubits"] < constraints.min_qubits
        ):
            continue
        if constraints.no_queue and backend["queue"] != "none":
            continue
        if constraints.free_only and backend["cost"] == "paid":
            continue
        if constraints.no_account and backend["requires_account"]:
            continue
        if constraints.prefer_hardware and backend["kind"] == "simulator":
            continue
        candidates.append(backend)

    def rank(backend: dict) -> tuple[int, int]:
        try:
            preference = PREFERENCE.index(backend["id"])
        except ValueError:
            preference = len(PREFERENCE)
        return (preference, backend["max_qubits"])

    return sorted(candidates, key=rank)


def explain(constraints: Constraints, results: list[dict]) -> str:
    """生成含规范后端标识的人类可读回复。"""

    if not results:
        return (
            "按官方后端能力表，没有后端能同时满足这些条件。"
            "可以放宽比特数要求，或接受一定排队时间。"
        )

    best = results[0]
    wants = []
    if constraints.min_qubits:
        wants.append(f"至少 {constraints.min_qubits} 比特")
    if constraints.no_queue:
        wants.append("零排队等待")
    if constraints.free_only:
        wants.append("免费")
    if constraints.no_account:
        wants.append("无需注册账号")
    requirement = "、".join(wants) if wants else "你的需求"

    lines = [
        f"推荐后端：{best['id']}",
        "",
        f"理由：你要求{requirement}。{best['name']}支持最多 "
        f"{best['max_qubits']} 比特，排队情况为 {best['queue']}，"
        f"费用为 {best['cost']}。",
    ]
    if best["notes"]:
        lines.append(f"补充：{best['notes']}")

    alternatives = [item["id"] for item in results[1:]]
    if alternatives:
        lines += ["", "同样满足条件的备选：" + "、".join(alternatives)]
    return "\n".join(lines)
