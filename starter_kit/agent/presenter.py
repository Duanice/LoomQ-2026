"""把电路翻译成零基础用户能看懂的东西：ASCII 电路图与大白话解释。"""

from __future__ import annotations

try:
    from ..qasm_parser import Circuit
except ImportError:  # Support `python starter_kit/...` 直接执行。
    from qasm_parser import Circuit


GATE_SYMBOL = {
    "h": "H", "x": "X", "s": "S", "sdg": "S†", "t": "T", "tdg": "T†",
    "rz": "Rz", "ry": "Ry",
}

PLAIN_WORDS = {
    "h": "把这个比特放进「既是 0 又是 1」的叠加态",
    "x": "把这个比特翻转（0 变 1，1 变 0）",
    "cx": "让两个比特联动：控制位是 1 时，目标位就翻转",
    "ccx": "两个控制位都是 1 时，目标位才翻转",
    "swap": "交换两个比特的状态",
    "s": "给这个比特加一个四分之一圈的相位",
    "t": "给这个比特加一个八分之一圈的相位",
    "rz": "绕 Z 轴旋转这个比特",
    "ry": "绕 Y 轴旋转这个比特",
}


def diagram(circuit: Circuit) -> str:
    """渲染 ASCII 电路图，每列一个门。"""

    rows = [[] for _ in range(circuit.qubit_count)]
    for operation in circuit.operations:
        cells = ["───"] * circuit.qubit_count
        if len(operation.qubits) == 1:
            symbol = GATE_SYMBOL.get(operation.name, operation.name.upper())
            cells[operation.qubits[0]] = f"─{symbol[:1]}─"
        elif operation.name in {"cx", "cu1"}:
            control, target = operation.qubits
            cells[control] = "─●─"
            cells[target] = "─X─" if operation.name == "cx" else "─◆─"
            for index in range(min(operation.qubits) + 1, max(operation.qubits)):
                cells[index] = "─┼─"
        elif operation.name == "swap":
            first, second = operation.qubits
            cells[first] = cells[second] = "─╳─"
            for index in range(min(operation.qubits) + 1, max(operation.qubits)):
                cells[index] = "─┼─"
        elif operation.name == "ccx":
            *controls, target = operation.qubits
            for control in controls:
                cells[control] = "─●─"
            cells[target] = "─X─"
            for index in range(min(operation.qubits) + 1, max(operation.qubits)):
                if cells[index] == "───":
                    cells[index] = "─┼─"
        for index, cell in enumerate(cells):
            rows[index].append(cell)

    measured = {measurement.qubit for measurement in circuit.measurements}
    lines = []
    for index, row in enumerate(rows):
        tail = "─M─" if index in measured else "───"
        lines.append(f"q{index} ─" + "".join(row) + tail)
    return "\n".join(lines)


def explain(circuit: Circuit, probabilities: dict[str, float]) -> str:
    """用大白话说明这个电路在做什么、结果该怎么读。"""

    parts = [
        f"这个电路用了 {circuit.qubit_count} 个量子比特，"
        f"一共 {len(circuit.operations)} 步操作。",
        "",
        "它做了什么：",
    ]
    seen = []
    for operation in circuit.operations:
        words = PLAIN_WORDS.get(operation.name)
        if words and words not in seen:
            seen.append(words)
    parts += [f"  · {item}" for item in seen]

    outcomes = sorted(probabilities.items(), key=lambda item: -item[1])
    likely = [item for item in outcomes if item[1] > 0.01]
    parts += ["", "测量之后你会看到什么："]
    if len(likely) == 1:
        parts.append(f"  结果几乎总是 {likely[0][0]}。")
    else:
        readable = "、".join(
            f"{key}（约 {value * 100:.0f}%）" for key, value in likely[:4]
        )
        parts.append(f"  结果会在这几种之间随机出现：{readable}。")
        if len(likely) == 2 and abs(likely[0][1] - likely[1][1]) < 0.05:
            parts.append(
                "  两种结果各占一半、而且中间的组合从不出现 —— "
                "这说明这些比特是「纠缠」的：它们总是一起给出同样的答案。"
            )
    parts += [
        "",
        "怎么读这些 0 和 1：每一位代表一个比特的测量结果，最右边那一位是 q0。",
    ]
    return "\n".join(parts)
