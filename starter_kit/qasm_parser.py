"""Parser for the OpenQASM 2.0 subset used by LoomQ L1."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Operation:
    name: str
    qubits: tuple[int, ...]
    parameter: str | None = None


@dataclass(frozen=True)
class Measurement:
    qubit: int
    cbit: int


@dataclass(frozen=True)
class Circuit:
    qubit_count: int
    cbit_count: int
    operations: tuple[Operation, ...]
    measurements: tuple[Measurement, ...]


# 门名: (量子比特数量, 是否必须带参数)
GATES = {
    "h": (1, False),
    "x": (1, False),
    "s": (1, False),
    "sdg": (1, False),
    "t": (1, False),
    "tdg": (1, False),
    "rz": (1, True),
    "ry": (1, True),
    "cx": (2, False),
    "cu1": (2, True),
    "swap": (2, False),
    "ccx": (3, False),
}

IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
QREG_RE = re.compile(rf"qreg\s+({IDENTIFIER})\[(\d+)]")
CREG_RE = re.compile(rf"creg\s+({IDENTIFIER})\[(\d+)]")
GATE_RE = re.compile(r"(?P<name>[a-z][a-z0-9]*)(?P<tail>.*)", re.DOTALL)
QUBIT_RE = re.compile(rf"({IDENTIFIER})\[(\d+)]")
BIT_MEASURE_RE = re.compile(
    rf"measure\s+({IDENTIFIER})\[(\d+)]\s*->\s*({IDENTIFIER})\[(\d+)]"
)
REGISTER_MEASURE_RE = re.compile(
    rf"measure\s+({IDENTIFIER})\s*->\s*({IDENTIFIER})"
)


def _statements(source: str) -> list[str]:
    source = re.sub(r"//[^\n]*", "", source)
    parts = source.split(";")
    if parts[-1].strip():
        raise ValueError("每条 OpenQASM 语句都必须以分号结尾")
    return [part.strip() for part in parts[:-1] if part.strip()]


def _parse_gate(
    statement: str,
    qreg_name: str,
    qubit_count: int,
) -> Operation:
    match = GATE_RE.fullmatch(statement)
    if not match:
        raise ValueError(f"无法解析语句: {statement!r}")

    name = match.group("name")
    if name not in GATES:
        raise ValueError(f"不支持的门: {name}")

    tail = match.group("tail").lstrip()
    parameter = None
    if tail.startswith("("):
        depth = 0
        for index, character in enumerate(tail):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    parameter = tail[1:index].strip()
                    tail = tail[index + 1 :].strip()
                    break
        else:
            raise ValueError(f"参数括号不匹配: {statement!r}")

    qubits = []
    for operand in tail.split(","):
        qubit = QUBIT_RE.fullmatch(operand.strip())
        if not qubit or qubit.group(1) != qreg_name:
            raise ValueError(f"非法量子比特: {operand.strip()!r}")
        index = int(qubit.group(2))
        if index >= qubit_count:
            raise ValueError(f"量子比特越界: {qreg_name}[{index}]")
        qubits.append(index)

    expected_arity, needs_parameter = GATES[name]
    if len(qubits) != expected_arity:
        raise ValueError(f"{name} 需要 {expected_arity} 个量子比特")
    if len(set(qubits)) != len(qubits):
        raise ValueError(f"{name} 的量子比特不能重复")
    if needs_parameter and not parameter:
        raise ValueError(f"{name} 必须带角度参数")
    if not needs_parameter and parameter is not None:
        raise ValueError(f"{name} 不接受参数")

    return Operation(name, tuple(qubits), parameter)


def parse_qasm(source: str) -> Circuit:
    """Parse one qreg, one creg, the 12 allowed gates, and final measurements."""

    statements = _statements(source)
    if not statements or statements[0] != "OPENQASM 2.0":
        raise ValueError("程序必须以 OPENQASM 2.0; 开头")

    qreg_name = creg_name = None
    qubit_count = cbit_count = None
    operations: list[Operation] = []
    measurements: list[Measurement] = []
    measurement_started = False

    for statement in statements[1:]:
        if statement.startswith("include "):
            continue

        qreg = QREG_RE.fullmatch(statement)
        if qreg:
            if qreg_name is not None:
                raise ValueError("当前提交子集只支持一个 qreg")
            qreg_name, qubit_count_text = qreg.groups()
            qubit_count = int(qubit_count_text)
            if qubit_count <= 0:
                raise ValueError("qreg 大小必须大于 0")
            continue

        creg = CREG_RE.fullmatch(statement)
        if creg:
            if creg_name is not None:
                raise ValueError("当前提交子集只支持一个 creg")
            creg_name, cbit_count_text = creg.groups()
            cbit_count = int(cbit_count_text)
            if cbit_count <= 0:
                raise ValueError("creg 大小必须大于 0")
            continue

        if qreg_name is None or creg_name is None:
            raise ValueError("必须先声明 qreg 和 creg")

        bit_measure = BIT_MEASURE_RE.fullmatch(statement)
        if bit_measure:
            source_name, qubit_text, target_name, cbit_text = bit_measure.groups()
            qubit, cbit = int(qubit_text), int(cbit_text)
            if source_name != qreg_name or target_name != creg_name:
                raise ValueError(f"非法测量语句: {statement!r}")
            if qubit >= qubit_count or cbit >= cbit_count:
                raise ValueError(f"测量下标越界: {statement!r}")
            measurements.append(Measurement(qubit, cbit))
            measurement_started = True
            continue

        register_measure = REGISTER_MEASURE_RE.fullmatch(statement)
        if register_measure:
            source_name, target_name = register_measure.groups()
            if source_name != qreg_name or target_name != creg_name:
                raise ValueError(f"非法测量语句: {statement!r}")
            if qubit_count != cbit_count:
                raise ValueError("整寄存器测量要求 qreg 和 creg 大小相同")
            measurements.extend(
                Measurement(index, index) for index in range(qubit_count)
            )
            measurement_started = True
            continue

        if measurement_started:
            raise ValueError("当前提交子集不支持测量后的量子门")
        operations.append(_parse_gate(statement, qreg_name, qubit_count))

    if qreg_name is None or creg_name is None:
        raise ValueError("程序必须声明 qreg 和 creg")
    if not measurements:
        raise ValueError("程序必须包含测量语句")

    return Circuit(
        qubit_count=qubit_count,
        cbit_count=cbit_count,
        operations=tuple(operations),
        measurements=tuple(measurements),
    )
