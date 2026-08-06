"""Target-code emitters for LoomQ L1."""

try:
    from .qasm_parser import Circuit
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from qasm_parser import Circuit


_ORIGIN_GATE_NAMES = {
    "h": "H",
    "x": "X",
    "s": "S",
    "sdg": "SDAG",
    "t": "T",
    "tdg": "TDAG",
    "ry": "RY",
    "rz": "RZ",
    "cx": "CNOT",
    "cu1": "CU1",
    "swap": "SWAP",
    "ccx": "TOFFOLI",
}


def emit_spinq(circuit: Circuit) -> str:
    """Emit a complete OpenQASM 2.0 program for SpinQ."""

    lines = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"qreg q[{circuit.qubit_count}];",
        f"creg c[{circuit.cbit_count}];",
    ]

    for operation in circuit.operations:
        parameter = f"({operation.parameter})" if operation.parameter else ""
        operands = ", ".join(f"q[{qubit}]" for qubit in operation.qubits)
        lines.append(f"{operation.name}{parameter} {operands};")

    lines.extend(
        f"measure q[{measurement.qubit}] -> c[{measurement.cbit}];"
        for measurement in circuit.measurements
    )
    return "\n".join(lines) + "\n"


def emit_originq(circuit: Circuit) -> str:
    """Emit the OriginIR subset required by the LoomQ contract."""

    lines = [f"QINIT {circuit.qubit_count}", f"CREG {circuit.cbit_count}"]
    for operation in circuit.operations:
        name = _ORIGIN_GATE_NAMES[operation.name]
        parameter = f"({operation.parameter})" if operation.parameter else ""
        operands = ", ".join(f"q[{qubit}]" for qubit in operation.qubits)
        lines.append(f"{name}{parameter} {operands}")
    lines.extend(
        f"MEASURE q[{measurement.qubit}], c[{measurement.cbit}]"
        for measurement in circuit.measurements
    )
    return "\n".join(lines) + "\n"
