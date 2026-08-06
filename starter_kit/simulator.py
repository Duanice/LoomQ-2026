"""Dependency-free statevector simulator for LoomQ's 12-gate L1 subset."""

import ast
import cmath
from collections import Counter
import math
import operator
import random

try:
    from .qasm_parser import Circuit, Operation
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from qasm_parser import Circuit, Operation


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def evaluate_angle(expression: str) -> float:
    """Evaluate numeric/pi arithmetic without using eval()."""

    tree = ast.parse(expression.replace("^", "**"), mode="eval")

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id == "pi":
            return math.pi
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError(f"不支持的角度表达式: {expression!r}")

    return visit(tree)


def _apply_single(
    state: list[complex],
    qubit: int,
    matrix: tuple[tuple[complex, complex], tuple[complex, complex]],
) -> None:
    step = 1 << qubit
    for start in range(0, len(state), 2 * step):
        for offset in range(step):
            zero = start + offset
            one = zero + step
            a, b = state[zero], state[one]
            state[zero] = matrix[0][0] * a + matrix[0][1] * b
            state[one] = matrix[1][0] * a + matrix[1][1] * b


def _apply_controlled_x(
    state: list[complex], controls: tuple[int, ...], target: int
) -> None:
    for index in range(len(state)):
        if all((index >> control) & 1 for control in controls) and not (
            (index >> target) & 1
        ):
            partner = index | (1 << target)
            state[index], state[partner] = state[partner], state[index]


def _apply_swap(state: list[complex], first: int, second: int) -> None:
    for index in range(len(state)):
        first_bit = (index >> first) & 1
        second_bit = (index >> second) & 1
        if first_bit == 0 and second_bit == 1:
            partner = index ^ (1 << first) ^ (1 << second)
            state[index], state[partner] = state[partner], state[index]


def _apply_operation(state: list[complex], operation: Operation) -> None:
    name = operation.name
    qubits = operation.qubits
    inverse_sqrt_two = 1 / math.sqrt(2)

    if name == "h":
        matrix = (
            (inverse_sqrt_two, inverse_sqrt_two),
            (inverse_sqrt_two, -inverse_sqrt_two),
        )
    elif name == "x":
        matrix = ((0, 1), (1, 0))
    elif name == "s":
        matrix = ((1, 0), (0, 1j))
    elif name == "sdg":
        matrix = ((1, 0), (0, -1j))
    elif name == "t":
        matrix = ((1, 0), (0, cmath.exp(1j * math.pi / 4)))
    elif name == "tdg":
        matrix = ((1, 0), (0, cmath.exp(-1j * math.pi / 4)))
    elif name == "ry":
        theta = evaluate_angle(operation.parameter or "")
        matrix = (
            (math.cos(theta / 2), -math.sin(theta / 2)),
            (math.sin(theta / 2), math.cos(theta / 2)),
        )
    elif name == "rz":
        theta = evaluate_angle(operation.parameter or "")
        matrix = (
            (cmath.exp(-1j * theta / 2), 0),
            (0, cmath.exp(1j * theta / 2)),
        )
    elif name == "cx":
        _apply_controlled_x(state, (qubits[0],), qubits[1])
        return
    elif name == "cu1":
        theta = evaluate_angle(operation.parameter or "")
        both_one = (1 << qubits[0]) | (1 << qubits[1])
        phase = cmath.exp(1j * theta)
        for index in range(len(state)):
            if index & both_one == both_one:
                state[index] *= phase
        return
    elif name == "swap":
        _apply_swap(state, qubits[0], qubits[1])
        return
    elif name == "ccx":
        _apply_controlled_x(state, qubits[:2], qubits[2])
        return
    else:
        raise ValueError(f"不支持的门: {name}")

    _apply_single(state, qubits[0], matrix)


def simulate(circuit: Circuit) -> list[complex]:
    state = [0j] * (1 << circuit.qubit_count)
    state[0] = 1 + 0j
    for operation in circuit.operations:
        _apply_operation(state, operation)
    return state


def sample_counts(circuit: Circuit, shots: int, seed: int) -> dict[str, int]:
    state = simulate(circuit)
    basis_states = random.Random(seed).choices(
        range(len(state)),
        weights=[abs(amplitude) ** 2 for amplitude in state],
        k=shots,
    )

    counts = Counter()
    for basis_state in basis_states:
        classical = [0] * circuit.cbit_count
        for measurement in circuit.measurements:
            classical[measurement.cbit] = (basis_state >> measurement.qubit) & 1
        counts["".join(str(bit) for bit in reversed(classical))] += 1
    return dict(sorted(counts.items()))
