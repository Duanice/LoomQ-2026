#!/usr/bin/env python3
"""Random differential defense tests for the LoomQ L3 compiler."""

import argparse
import itertools
import json
import random
import re
import sys

try:
    from .adapter import compile_hybrid
    from .qasm_parser import GATES, parse_qasm
    from .riscv_emulator import TinyRISCVEmulator
except ImportError:  # Support `python l3_defense.py` inside the Docker image.
    from adapter import compile_hybrid
    from qasm_parser import GATES, parse_qasm
    from riscv_emulator import TinyRISCVEmulator


ALLOWED_INSTRUCTIONS = {"li", "add", "sub", "addi", "beq", "bne", "j"}


def _expression(rng: random.Random, cbits: int, depth: int):
    if depth == 0 or rng.random() < 0.4:
        kind = rng.choice(("literal", "register", "cbit"))
        if kind == "literal":
            return (kind, rng.randint(-50, 50))
        if kind == "register":
            return (kind, rng.randint(1, 9))
        return (kind, rng.randrange(cbits))
    return (
        "binary",
        rng.choice(("+", "-")),
        _expression(rng, cbits, depth - 1),
        _expression(rng, cbits, depth - 1),
    )


def _block(
    rng: random.Random,
    cbits: int,
    depth: int,
    allow_empty: bool = False,
    budget: list[int] | None = None,
    force_depth: bool = False,
):
    """Generate a bounded block; the official emulator allows 1,000 steps."""
    budget = budget if budget is not None else [24]
    if not budget[0]:
        return ()
    minimum = 1 if force_depth or not allow_empty else 0
    count = rng.randint(minimum, min(2, budget[0]))
    statements = []
    for index in range(count):
        if not budget[0]:
            break
        must_branch = force_depth and index == 0 and depth > 0
        budget[0] -= 1
        if depth and (must_branch or rng.random() < 0.45):
            then_body = _block(
                rng,
                cbits,
                depth - 1,
                allow_empty=True,
                budget=budget,
                force_depth=must_branch,
            )
            else_body = (
                _block(
                    rng,
                    cbits,
                    depth - 1,
                    allow_empty=True,
                    budget=budget,
                )
                if budget[0] and rng.random() < 0.85
                else None
            )
            statements.append(
                (
                    "if",
                    rng.choice(("==", "!=")),
                    _expression(rng, cbits, 2),
                    _expression(rng, cbits, 2),
                    then_body,
                    else_body,
                )
            )
        else:
            statements.append(
                ("assign", rng.randint(1, 9), _expression(rng, cbits, 3))
            )
    return tuple(statements)


def _render_expression(expression) -> str:
    if expression[0] == "literal":
        return str(expression[1])
    if expression[0] == "register":
        return f"r{expression[1]}"
    if expression[0] == "cbit":
        return f"c[{expression[1]}]"
    return (
        f"({_render_expression(expression[2])} {expression[1]} "
        f"{_render_expression(expression[3])})"
    )


def _render_block(statements, indent: int = 1) -> list[str]:
    lines = []
    prefix = "    " * indent
    for statement in statements:
        if statement[0] == "assign":
            lines.append(
                f"{prefix}r{statement[1]} = {_render_expression(statement[2])};"
            )
            continue
        lines.append(
            f"{prefix}if ({_render_expression(statement[2])} {statement[1]} "
            f"{_render_expression(statement[3])}) {{"
        )
        lines.extend(_render_block(statement[4], indent + 1))
        lines.append(f"{prefix}}}")
        if statement[5] is not None:
            lines[-1] += " else {"
            lines.extend(_render_block(statement[5], indent + 1))
            lines.append(f"{prefix}}}")
    return lines


def _evaluate(expression, registers: list[int], measured: tuple[int, ...]) -> int:
    if expression[0] == "literal":
        return expression[1]
    if expression[0] == "register":
        return registers[expression[1]]
    if expression[0] == "cbit":
        return measured[expression[1]]
    left = _evaluate(expression[2], registers, measured)
    right = _evaluate(expression[3], registers, measured)
    return left + right if expression[1] == "+" else left - right


def _interpret(statements, measured: tuple[int, ...], registers=None) -> list[int]:
    registers = registers or [0] * 10
    for statement in statements:
        if statement[0] == "assign":
            registers[statement[1]] = _evaluate(statement[2], registers, measured)
            continue
        left = _evaluate(statement[2], registers, measured)
        right = _evaluate(statement[3], registers, measured)
        condition = left == right
        if statement[1] == "!=":
            condition = not condition
        selected = statement[4] if condition else statement[5]
        if selected is not None:
            _interpret(selected, measured, registers)
    return registers


def _angle(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return f"{rng.choice([value for value in range(-8, 9) if value])}*pi/{rng.choice((2, 3, 4, 5, 7))}"
    return f"pi/({rng.randint(1, 4)}+{rng.randint(1, 4)})"


def _quantum_statements(rng: random.Random, qubits: int, cbits: int):
    valid = [name for name, (arity, _) in GATES.items() if arity <= qubits]
    statements = []
    for _ in range(rng.randint(1, 20)):
        name = rng.choice(valid)
        arity, parameterized = GATES[name]
        operands = ", ".join(
            f"q[{index}]" for index in rng.sample(range(qubits), arity)
        )
        parameter = f"({_angle(rng)})" if parameterized else ""
        statements.append(f"{name}{parameter} {operands};")
    if qubits == cbits and rng.random() < 0.3:
        measurements = ["measure q -> c;"]
    else:
        measurements = [
            f"measure q[{index % qubits}] -> c[{index}];" for index in range(cbits)
        ]
    # Exercise the L3 contract's source-order preservation, including gates after
    # measurements; the L1 parser validation below reorders only its test copy.
    for measurement in measurements:
        statements.insert(rng.randrange(len(statements) + 1), measurement)
    return statements


def _build_case(
    seed: int, max_cbits: int, max_depth: int, force_depth: bool = False
):
    rng = random.Random(seed)
    cbits = rng.randint(1, max_cbits)
    qubits = rng.randint(cbits, 5)
    statements = _block(
        rng, cbits, max_depth, budget=[24], force_depth=force_depth
    )
    quantum = _quantum_statements(rng, qubits, cbits)
    insertion = rng.randrange(len(quantum) + 1)
    classical = ["classical {", *_render_block(statements), "}"]
    lines = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"qreg q[{qubits}];",
        f"creg c[{cbits}];",
        "// The hidden-style generator may place classical control mid-circuit.",
        *quantum[:insertion],
        *classical,
        *quantum[insertion:],
        "",
    ]
    return "\n".join(lines), quantum, statements, qubits, cbits


def _validate_quantum(
    actual: list[str], expected: list[str], qubits: int, cbits: int
) -> set[str]:
    if actual != expected:
        raise AssertionError("quantum operation list changed content or order")
    if any(
        not isinstance(statement, str) or not statement.endswith(";")
        for statement in actual
    ):
        raise AssertionError("quantum operations must be complete QASM statements")
    if any(
        re.match(r"(?:OPENQASM|include|qreg|creg|classical)\b", statement)
        for statement in actual
    ):
        raise AssertionError("quantum operation list contains non-operation syntax")

    gates = [statement for statement in actual if not statement.startswith("measure ")]
    measurements = [statement for statement in actual if statement.startswith("measure ")]
    reconstructed = "\n".join(
        [
            "OPENQASM 2.0;",
            'include "qelib1.inc";',
            f"qreg q[{qubits}];",
            f"creg c[{cbits}];",
            *gates,
            *measurements,
            "",
        ]
    )
    circuit = parse_qasm(reconstructed)
    measurement_count = sum(
        cbits if statement == "measure q -> c;" else 1
        for statement in measurements
    )
    if (
        len(circuit.operations) != len(gates)
        or len(circuit.measurements) != measurement_count
    ):
        raise AssertionError("quantum operation list failed independent QASM validation")
    return {operation.name for operation in circuit.operations}


def _validate_assembly(assembly: str) -> set[str]:
    instructions = set()
    labels = set()
    for line in assembly.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":"):
            label = line[:-1]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label) or label in labels:
                raise AssertionError(f"invalid or duplicate label: {label!r}")
            labels.add(label)
            continue
        instruction = line.split()[0]
        if instruction not in ALLOWED_INSTRUCTIONS:
            raise AssertionError(f"unsupported instruction: {instruction}")
        instructions.add(instruction)
    return instructions


def _nesting_depth(statements) -> int:
    depths = [0]
    for statement in statements:
        if statement[0] == "if":
            depths.append(1 + _nesting_depth(statement[4]))
            if statement[5] is not None:
                depths.append(1 + _nesting_depth(statement[5]))
    return max(depths)


def run_defense(seed: int, programs: int, max_cbits: int, max_depth: int) -> dict:
    master = random.Random(seed)
    failures = []
    executions = 0
    quantum_operations = 0
    observed_depth = 0
    gate_coverage = set()
    instruction_coverage = set()

    for index in range(programs):
        case_seed = master.randrange(2**32)
        source, expected_quantum, statements, qubits, cbits = _build_case(
            case_seed, max_cbits, max_depth, force_depth=index == 0
        )
        try:
            actual_quantum, assembly = compile_hybrid(source)
            gate_coverage.update(
                _validate_quantum(actual_quantum, expected_quantum, qubits, cbits)
            )
            instruction_coverage.update(_validate_assembly(assembly))
            quantum_operations += len(actual_quantum)
            observed_depth = max(observed_depth, _nesting_depth(statements))

            for measured in itertools.product((0, 1), repeat=cbits):
                expected = _interpret(statements, measured)
                emulator = TinyRISCVEmulator()
                emulator.load_program(assembly)
                for bit, value in enumerate(measured):
                    emulator.set_register(f"x{10 + bit}", value)
                actual = emulator.execute()
                executions += 1
                for register in range(1, 10):
                    if actual.get(f"x{register}", 0) != expected[register]:
                        raise AssertionError(
                            f"x{register} mismatch for measured={measured}"
                        )
                for bit, value in enumerate(measured):
                    if actual.get(f"x{10 + bit}", 0) != value:
                        raise AssertionError(f"c[{bit}] input was modified")
                for register in range(10 + cbits, 32):
                    if actual.get(f"x{register}", 0):
                        raise AssertionError(f"scratch x{register} was not cleared")
        except Exception as exc:
            failures.append(
                {
                    "case": index,
                    "seed": case_seed,
                    "error": f"{type(exc).__name__}: {exc}",
                    "source": source[:4000],
                    "source_truncated": len(source) > 4000,
                }
            )
        if (index + 1) % 50 == 0 or index + 1 == programs:
            print(f"[{index + 1}/{programs}] programs completed", flush=True)

    missing_gates = sorted(set(GATES) - gate_coverage)
    missing_instructions = sorted(ALLOWED_INSTRUCTIONS - instruction_coverage)
    if missing_gates:
        failures.append({"case": "gate-coverage", "error": f"missing: {missing_gates}"})
    if missing_instructions:
        failures.append(
            {"case": "instruction-coverage", "error": f"missing: {missing_instructions}"}
        )
    if observed_depth < max_depth:
        failures.append(
            {
                "case": "nesting-coverage",
                "error": f"observed depth {observed_depth} < requested {max_depth}",
            }
        )

    return {
        "seed": seed,
        "programs": programs,
        "measurement_executions": executions,
        "quantum_operations_validated": quantum_operations,
        "max_cbits": max_cbits,
        "max_nesting_depth": observed_depth,
        "gate_coverage": sorted(gate_coverage),
        "instruction_coverage": sorted(instruction_coverage),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--programs", type=int, default=500)
    parser.add_argument("--max-cbits", type=int, default=4)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if args.programs <= 0 or not 1 <= args.max_cbits <= 5 or args.max_depth < 0:
        parser.error("programs must be positive, max-cbits 1..5, max-depth non-negative")

    report = run_defense(args.seed, args.programs, args.max_cbits, args.max_depth)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    summary = {key: value for key, value in report.items() if key != "failures"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["failures"]:
        print(json.dumps({"failures": report["failures"]}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
