"""电路自验：解析 + 模拟 + 保真度校验。

复用 L1 的 qasm_parser 与 simulator，不重复实现模拟逻辑。
本模块不依赖 LLM，可独立测试与运行。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

try:
    from ..qasm_parser import Circuit, parse_qasm
    from ..simulator import result_probabilities, simulate
except ImportError:  # Support `python starter_kit/...` 直接执行。
    from qasm_parser import Circuit, parse_qasm
    from simulator import result_probabilities, simulate


# 题面规定意图生成/代码纠错的通过线。
FIDELITY_THRESHOLD = 0.97

QASM_FENCE_RE = re.compile(
    r"```(?:qasm|openqasm)?\s*(.+?)```", re.DOTALL | re.IGNORECASE
)
QASM_BARE_RE = re.compile(r"(OPENQASM\s+2\.0\s*;.*)", re.DOTALL | re.IGNORECASE)


def extract_qasm(text: str) -> str | None:
    """从模型回复中提取 QASM：优先代码围栏，退化为扫 OPENQASM 起始段。"""

    for candidate in QASM_FENCE_RE.findall(text):
        if "OPENQASM" in candidate.upper():
            return candidate.strip()

    bare = QASM_BARE_RE.search(text)
    if bare:
        body = bare.group(1)
        # 去掉围栏残留与结尾解释文字：只保留到最后一个分号。
        body = body.replace("```", "")
        last = body.rfind(";")
        if last != -1:
            return body[: last + 1].strip()
    return None


# ---------------------------------------------------------------------------
# 目标态


def _normalise(vector: list[complex]) -> list[complex]:
    norm = math.sqrt(sum(abs(amplitude) ** 2 for amplitude in vector))
    if norm == 0:
        raise ValueError("目标态范数为零")
    return [amplitude / norm for amplitude in vector]


def target_statevector(kind: str, qubit_count: int) -> list[complex] | None:
    """构造已知目标态。无法识别时返回 None，调用方应降级为只校验语法。"""

    if qubit_count <= 0:
        return None
    size = 1 << qubit_count
    kind = (kind or "").strip().lower()

    if kind in {"ghz", "greenberger-horne-zeilinger"}:
        vector = [0j] * size
        vector[0] = 1 + 0j
        vector[size - 1] = 1 + 0j
        return _normalise(vector)

    if kind in {"bell", "epr"}:
        if qubit_count != 2:
            return None
        vector = [0j] * size
        vector[0] = 1 + 0j
        vector[3] = 1 + 0j
        return _normalise(vector)

    if kind in {"w"}:
        vector = [0j] * size
        for bit in range(qubit_count):
            vector[1 << bit] = 1 + 0j
        return _normalise(vector)

    if kind in {"uniform", "plus", "superposition"}:
        return _normalise([1 + 0j] * size)

    return None


def fidelity(generated: list[complex], target: list[complex]) -> float:
    """|<target|generated>|^2 —— 全局相位无关。"""

    if len(generated) != len(target):
        raise ValueError("态矢量维度不一致")
    overlap = sum(t.conjugate() * g for g, t in zip(generated, target))
    return abs(overlap) ** 2


# ---------------------------------------------------------------------------
# 校验结果


@dataclass
class VerificationResult:
    ok: bool
    stage: str  # parse | fidelity | syntax_only
    message: str
    qasm: str | None = None
    circuit: Circuit | None = None
    fidelity: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)

    @property
    def feedback(self) -> str:
        """回灌给 LLM 的重试提示 —— 必须具体，不能只说「错了」。"""
        if self.ok:
            return ""
        if self.stage == "parse":
            return (
                "上一次生成的 QASM 无法解析，解析器报错："
                f"{self.message}\n请修正后重新输出完整的 OpenQASM 2.0 代码。"
            )
        if self.stage == "fidelity":
            observed = ", ".join(
                f"{key}={value:.3f}"
                for key, value in sorted(
                    self.probabilities.items(), key=lambda item: -item[1]
                )[:6]
            )
            return (
                f"上一次生成的电路语法正确，但保真度只有 {self.fidelity:.3f}"
                f"（需要 ≥ {FIDELITY_THRESHOLD}）。\n"
                f"实际测量分布为：{observed}\n"
                "请检查门序列是否真正实现了目标态，然后重新输出完整代码。"
            )
        return self.message


def verify(
    qasm: str,
    target_state: str | None = None,
    qubit_count: int | None = None,
) -> VerificationResult:
    """校验一段 QASM。目标态未知时只做语法校验，不强行拦截。"""

    try:
        circuit = parse_qasm(qasm)
    except ValueError as exc:
        return VerificationResult(False, "parse", str(exc), qasm=qasm)

    probabilities = {
        key: value
        for key, value in result_probabilities(circuit).items()
        if value > 1e-12
    }

    expected = target_statevector(
        target_state or "", qubit_count or circuit.qubit_count
    )
    if expected is None or len(expected) != (1 << circuit.qubit_count):
        # 识别不出目标态：宁可不拦截，也不要误杀正确答案。
        return VerificationResult(
            True,
            "syntax_only",
            "语法校验通过（目标态未知，跳过保真度校验）",
            qasm=qasm,
            circuit=circuit,
            probabilities=probabilities,
        )

    score = fidelity(simulate(circuit), expected)
    passed = score >= FIDELITY_THRESHOLD
    return VerificationResult(
        passed,
        "fidelity",
        (
            f"自检通过 —— 保真度 {score:.3f}"
            if passed
            else f"保真度 {score:.3f} 低于阈值 {FIDELITY_THRESHOLD}"
        ),
        qasm=qasm,
        circuit=circuit,
        fidelity=score,
        probabilities=probabilities,
    )
