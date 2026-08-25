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
RELATION_EQUIVALENCE_TOLERANCE = 1e-9

QASM_FENCE_RE = re.compile(
    r"```(?:qasm|openqasm)?\s*(.+?)```", re.DOTALL | re.IGNORECASE
)
QASM_BARE_RE = re.compile(r"(OPENQASM\s+2\.0\s*;.*)", re.DOTALL | re.IGNORECASE)


def _qasm2_envelope_error(qasm: str) -> str | None:
    """Require the exact QASM 2 library header promised by the L2 contract."""

    source = re.sub(r"//[^\n]*", "", qasm)
    statements = [part.strip() for part in source.split(";") if part.strip()]
    if statements and statements[0] == "OPENQASM 2.0":
        required = 'include "qelib1.inc"'
        if len(statements) < 2 or statements[1] != required:
            return 'OPENQASM 2.0; 后必须紧跟 include "qelib1.inc";'
        if any(statement.startswith("include ") for statement in statements[2:]):
            return 'include "qelib1.inc"; 必须且只能出现一次，不能包含其他门库'
    return None


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


def circuits_are_distinct(first: Circuit, second: Circuit, basis: str) -> bool:
    """Compare two trusted Circuit IR values using a validated relation basis."""

    if basis == "circuit_structure":
        return first != second
    if basis == "quantum_state":
        if first.qubit_count != second.qubit_count:
            return True
        return fidelity(simulate(first), simulate(second)) < (
            1 - RELATION_EQUIVALENCE_TOLERANCE
        )
    if basis == "measurement_distribution":
        first_probabilities = result_probabilities(first)
        second_probabilities = result_probabilities(second)
        score = sum(
            math.sqrt(
                first_probabilities.get(key, 0.0)
                * second_probabilities.get(key, 0.0)
            )
            for key in first_probabilities.keys() | second_probabilities.keys()
        ) ** 2
        return score < (1 - RELATION_EQUIVALENCE_TOLERANCE)
    raise ValueError(f"unsupported circuit relationship basis: {basis}")


# ---------------------------------------------------------------------------
# 校验结果


@dataclass
class VerificationResult:
    ok: bool
    stage: str  # parse | simulate | qubit_count | fidelity | distribution | syntax_only
    message: str
    qasm: str | None = None
    circuit: Circuit | None = None
    fidelity: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    expected_probabilities: dict[str, float] = field(default_factory=dict)

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
        if self.stage == "simulate":
            return (
                f"上一次生成的 QASM 无法模拟：{self.message}\n"
                "角度只能使用有限数值、pi、括号和 + - * / ^ 运算，不能除以零。"
                "请修正后重新输出完整代码。"
            )
        if self.stage == "qubit_count":
            return (
                f"上一次生成的电路与目标比特数不一致：{self.message}\n"
                "请按目标比特数重新声明 qreg、creg，生成完整电路并测量全部目标比特。"
            )
        if self.stage in {"fidelity", "distribution"}:
            observed = ", ".join(
                f"{key}={value:.3f}"
                for key, value in sorted(
                    self.probabilities.items(), key=lambda item: -item[1]
                )[:6]
            )
            target = "目标测量分布" if self.stage == "distribution" else "目标态"
            expected = ", ".join(
                f"{key}={value:.3f}"
                for key, value in self.expected_probabilities.items()
            )
            distribution_hint = (
                f"\n目标测量分布为：{expected}\n"
                "位串按 c[n-1]...c[0] 显示：最左位对应 q[n-1]，最右位对应 q[0]。"
                if expected
                else ""
            )
            return (
                f"上一次生成的电路语法正确，但与{target}的保真度只有 {self.fidelity:.3f}"
                f"（需要 ≥ {FIDELITY_THRESHOLD}）。\n"
                f"实际测量分布为：{observed}{distribution_hint}\n"
                "请检查门序列是否真正实现了目标态，然后重新输出完整代码。"
            )
        return self.message


def verify(
    qasm: str,
    target_state: str | None = None,
    qubit_count: int | None = None,
    expected_probabilities: dict[str, float] | None = None,
) -> VerificationResult:
    """校验一段 QASM。目标态未知时只做语法校验，不强行拦截。"""

    envelope_error = _qasm2_envelope_error(qasm)
    if envelope_error:
        return VerificationResult(False, "parse", envelope_error, qasm=qasm)

    try:
        circuit = parse_qasm(qasm)
    except ValueError as exc:
        return VerificationResult(False, "parse", str(exc), qasm=qasm)

    if qubit_count is not None and circuit.qubit_count != qubit_count:
        return VerificationResult(
            False,
            "qubit_count",
            f"目标要求 {qubit_count} 个量子比特，生成电路却声明了 {circuit.qubit_count} 个",
            qasm=qasm,
            circuit=circuit,
        )

    try:
        probabilities = {
            key: value
            for key, value in result_probabilities(circuit).items()
            if value > 1e-12
        }
    except (SyntaxError, ArithmeticError, ValueError) as exc:
        return VerificationResult(
            False,
            "simulate",
            f"非法角度表达式或电路参数：{exc}",
            qasm=qasm,
            circuit=circuit,
        )

    if expected_probabilities:
        valid = all(
            isinstance(key, str)
            and len(key) == circuit.cbit_count
            and not (set(key) - {"0", "1"})
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
            for key, value in expected_probabilities.items()
        )
        total = sum(expected_probabilities.values()) if valid else 0
        if total <= 0:
            return VerificationResult(
                False,
                "distribution",
                "目标测量分布无效",
                qasm=qasm,
                circuit=circuit,
                fidelity=0.0,
                probabilities=probabilities,
            )
        expected = {
            key: value / total for key, value in expected_probabilities.items()
        }
        score = sum(
            math.sqrt(probabilities.get(key, 0.0) * probability)
            for key, probability in expected.items()
        ) ** 2
        passed = score >= FIDELITY_THRESHOLD
        return VerificationResult(
            passed,
            "distribution",
            (
                f"目标分布自检通过 —— 保真度 {score:.3f}"
                if passed
                else f"分布保真度 {score:.3f} 低于阈值 {FIDELITY_THRESHOLD}"
            ),
            qasm=qasm,
            circuit=circuit,
            fidelity=score,
            probabilities=probabilities,
            expected_probabilities=expected,
        )

    expected = target_statevector(
        target_state or "", qubit_count or circuit.qubit_count
    )
    if expected is None:
        # 识别不出目标态：宁可不拦截，也不要误杀正确答案。
        return VerificationResult(
            True,
            "syntax_only",
            "语法校验通过（目标态未知，跳过保真度校验）",
            qasm=qasm,
            circuit=circuit,
            probabilities=probabilities,
        )
    if len(expected) != (1 << circuit.qubit_count):
        expected_qubits = len(expected).bit_length() - 1
        return VerificationResult(
            False,
            "qubit_count",
            f"目标态需要 {expected_qubits} 个量子比特，生成电路却声明了 {circuit.qubit_count} 个",
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
