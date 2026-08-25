#!/usr/bin/env python3
"""Hidden-style L1 differential tests for all three local platform drivers."""

import argparse
import json
import math
import random
import re
import sys

try:
    from . import adapter
    from .evaluator import calculate_hellinger_fidelity, validate_schema
    from .qasm_parser import Circuit, Measurement, Operation, parse_qasm
    from .simulator import result_probabilities
except ImportError:  # Support `python l1_defense.py` inside the Docker image.
    import adapter
    from evaluator import calculate_hellinger_fidelity, validate_schema
    from qasm_parser import Circuit, Measurement, Operation, parse_qasm
    from simulator import result_probabilities


TARGETS = ("spinq", "originq", "braket")
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
ORIGIN_NAMES = {
    "H": "h",
    "X": "x",
    "S": "s",
    "SDAG": "sdg",
    "T": "t",
    "TDAG": "tdg",
    "RY": "ry",
    "RZ": "rz",
    "CNOT": "cx",
    "CU1": "cu1",
    "CR": "cu1",
    "SWAP": "swap",
    "TOFFOLI": "ccx",
    "CCX": "ccx",
}
BRAKET_NAMES = {"cnot": "cx", "cp": "cu1"}


def _program(qubits: int, operations: list[str], mapping=None) -> str:
    mapping = mapping or list(range(qubits))
    measurements = [
        f"measure q[{qubit}] -> c[{mapping[qubit]}];"
        for qubit in range(qubits)
    ]
    return "\n".join(
        [
            "OPENQASM 2.0;",
            'include "qelib1.inc";',
            f"qreg q[{qubits}];",
            f"creg c[{qubits}];",
            *operations,
            *measurements,
            "",
        ]
    )


def ghz5() -> str:
    return _program(5, ["h q[0];", *(f"cx q[0],q[{i}];" for i in range(1, 5))])


def qft4() -> str:
    operations = [
        "x q[0];",
        "h q[1];",
        "ry(pi/3) q[2];",
        "rz(pi/5) q[2];",
        "h q[3];",
        "t q[3];",
    ]
    for target in range(4):
        operations.append(f"h q[{target}];")
        for control in range(target + 1, 4):
            denominator = 2 ** (control - target)
            operations.append(
                f"cu1(pi/{denominator}) q[{control}],q[{target}];"
            )
    operations.extend(("swap q[0],q[3];", "swap q[1],q[2];"))
    return _program(4, operations)


def grover3() -> str:
    operations = [*(f"h q[{i}];" for i in range(3))]
    operations.extend(("h q[2];", "ccx q[0],q[1],q[2];", "h q[2];"))
    operations.extend(f"h q[{i}];" for i in range(3))
    operations.extend(f"x q[{i}];" for i in range(3))
    operations.extend(("h q[2];", "ccx q[0],q[1],q[2];", "h q[2];"))
    operations.extend(f"x q[{i}];" for i in range(3))
    operations.extend(f"h q[{i}];" for i in range(3))
    return _program(3, operations)


def _angle(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return format(rng.uniform(-2 * math.pi, 2 * math.pi), ".12g")
    numerator = rng.choice([i for i in range(-16, 17) if i])
    return f"{numerator}*pi/{rng.choice((2, 3, 4, 5, 7, 8))}"


def random_circuit(qubits: int, depth: int, seed: int) -> tuple[str, set[str]]:
    rng = random.Random(seed)
    valid = [name for name, (arity, _) in GATES.items() if arity <= qubits]
    operations = []
    used = set()
    for _ in range(depth):
        name = rng.choice(valid)
        arity, parameterized = GATES[name]
        operands = ",".join(f"q[{i}]" for i in rng.sample(range(qubits), arity))
        parameter = f"({_angle(rng)})" if parameterized else ""
        operations.append(f"{name}{parameter} {operands};")
        used.add(name)
    mapping = list(range(qubits))
    rng.shuffle(mapping)
    return _program(qubits, operations, mapping), used


def _origin_circuit(source: str) -> Circuit:
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    qinit = re.fullmatch(r"QINIT (\d+)", lines[0])
    creg = re.fullmatch(r"CREG (\d+)", lines[1])
    if not qinit or not creg:
        raise ValueError("invalid OriginIR declarations")
    operations = []
    measurements = []
    for line in lines[2:]:
        measured = re.fullmatch(r"MEASURE q\[(\d+)], c\[(\d+)]", line)
        if measured:
            measurements.append(Measurement(*(int(value) for value in measured.groups())))
            continue
        gate = re.fullmatch(r"([A-Z0-9]+)(?:\(([^()]*)\))? (.+)", line)
        if not gate or gate.group(1) not in ORIGIN_NAMES:
            raise ValueError(f"invalid OriginIR operation: {line}")
        qubits = tuple(int(value) for value in re.findall(r"q\[(\d+)]", gate.group(3)))
        operations.append(Operation(ORIGIN_NAMES[gate.group(1)], qubits, gate.group(2)))
    return Circuit(
        int(qinit.group(1)),
        int(creg.group(1)),
        tuple(operations),
        tuple(measurements),
    )


def _braket_circuit(source: str) -> Circuit:
    from openqasm3.parser import parse

    parse(source)  # Use the installed OpenQASM 3 parser for an independent syntax check.
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    qreg = re.fullmatch(r"qubit\[(\d+)] q;", lines[2])
    creg = re.fullmatch(r"bit\[(\d+)] c;", lines[3])
    if lines[:2] != ["OPENQASM 3.0;", 'include "stdgates.inc";'] or not qreg or not creg:
        raise ValueError("invalid Braket OpenQASM 3 declarations")
    operations = []
    measurements = []
    for line in lines[4:]:
        measured = re.fullmatch(r"c\[(\d+)] = measure q\[(\d+)];", line)
        if measured:
            cbit, qubit = (int(value) for value in measured.groups())
            measurements.append(Measurement(qubit, cbit))
            continue
        gate = re.fullmatch(r"([a-z][a-z0-9]*)(?:\(([^()]*)\))? (.+);", line)
        if not gate:
            raise ValueError(f"invalid Braket operation: {line}")
        qubits = tuple(int(value) for value in re.findall(r"q\[(\d+)]", gate.group(3)))
        operations.append(
            Operation(BRAKET_NAMES.get(gate.group(1), gate.group(1)), qubits, gate.group(2))
        )
    return Circuit(
        int(qreg.group(1)),
        int(creg.group(1)),
        tuple(operations),
        tuple(measurements),
    )


def validate_transpiles(qasm: str, circuit: Circuit) -> None:
    translated = {
        "spinq": parse_qasm(adapter.transpile(qasm, "spinq")),
        "originq": _origin_circuit(adapter.transpile(qasm, "originq")),
        "braket": _braket_circuit(adapter.transpile(qasm, "braket")),
    }
    for target, recovered in translated.items():
        if recovered != circuit:
            raise AssertionError(f"{target} transpile semantics differ from Circuit IR")


def build_cases(seed: int, random_per_size: int):
    cases = [("ghz-5", ghz5()), ("qft-4", qft4()), ("grover-3", grover3())]
    covered = set()
    for label, depth, case_seed in (
        ("random-shallow", 3, seed + 1),
        ("random-medium", 15, seed + 2),
        ("random-deep", 30, seed + 3),
    ):
        qasm, used = random_circuit(5, depth, case_seed)
        cases.append((f"{label}:seed={case_seed}", qasm))
        covered.update(used)

    master = random.Random(seed)
    for qubits in range(1, 6):
        for index in range(random_per_size):
            case_seed = master.randrange(2**32)
            depth = master.randint(1, 30)
            qasm, used = random_circuit(qubits, depth, case_seed)
            cases.append(
                (f"random:q={qubits}:i={index}:d={depth}:seed={case_seed}", qasm)
            )
            covered.update(used)
    return cases, covered


def run_defense(shots: int, seed: int, random_per_size: int) -> dict:
    cases, covered = build_cases(seed, random_per_size)
    failures = []
    stats = {
        target: {"passed": 0, "minimum_fidelity": 1.0, "worst_case": None}
        for target in TARGETS
    }

    for case_index, (label, qasm) in enumerate(cases, 1):
        circuit = parse_qasm(qasm)
        try:
            validate_transpiles(qasm, circuit)
        except Exception as exc:
            failures.append(
                {"case": label, "target": "transpile", "error": str(exc), "qasm": qasm}
            )
            continue

        expected = result_probabilities(circuit)
        for target in TARGETS:
            try:
                result = adapter.run(qasm, target, shots)
                valid, reason = validate_schema(result)
                if not valid:
                    raise AssertionError(reason)
                observed = {key: count / shots for key, count in result["counts"].items()}
                fidelity = calculate_hellinger_fidelity(observed, expected)
                if fidelity < stats[target]["minimum_fidelity"]:
                    stats[target]["minimum_fidelity"] = fidelity
                    stats[target]["worst_case"] = label
                if fidelity < 0.97:
                    raise AssertionError(f"Hellinger fidelity {fidelity:.6f} < 0.97")
                stats[target]["passed"] += 1
            except Exception as exc:
                failures.append(
                    {"case": label, "target": target, "error": str(exc), "qasm": qasm}
                )
        if case_index % 10 == 0 or case_index == len(cases):
            print(f"[{case_index}/{len(cases)}] cases completed", flush=True)

    missing_gates = sorted(set(GATES) - covered)
    if missing_gates:
        failures.append(
            {"case": "gate-coverage", "target": "generator", "error": f"missing: {missing_gates}"}
        )
    for target in TARGETS:
        stats[target]["minimum_fidelity"] = round(stats[target]["minimum_fidelity"], 6)
    return {
        "seed": seed,
        "shots": shots,
        "cases": len(cases),
        "executions": len(cases) * len(TARGETS),
        "transpile_validations": len(cases) * len(TARGETS),
        "gate_coverage": sorted(covered),
        "targets": stats,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--random-per-size", type=int, default=20)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if args.shots <= 0 or args.random_per_size <= 0:
        parser.error("shots and random-per-size must be positive")

    report = run_defense(args.shots, args.seed, args.random_per_size)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "failures"}, ensure_ascii=False, indent=2))
    if report["failures"]:
        print(json.dumps({"failures": report["failures"]}, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
