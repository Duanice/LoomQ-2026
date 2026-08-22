"""Deterministic ASCII rendering for a validated Circuit IR."""

from __future__ import annotations

try:
    from ..qasm_parser import Circuit
except ImportError:  # Support `python starter_kit/...` 直接执行。
    from qasm_parser import Circuit


GATE_SYMBOL = {
    "h": "H", "x": "X", "s": "S", "sdg": "S†", "t": "T", "tdg": "T†",
    "rz": "Rz", "ry": "Ry",
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
