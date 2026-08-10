"""Local simulator drivers for the SpinQ, OriginQ, and AWS Braket targets."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from .qasm_parser import Circuit
    from .simulator import evaluate_angle
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from qasm_parser import Circuit
    from simulator import evaluate_angle


_ORIGIN_RUNTIME_NAMES = {
    "h": "H",
    "x": "X",
    "s": "S",
    "t": "T",
    "cx": "CNOT",
    "swap": "SWAP",
    "ccx": "TOFFOLI",
}

_BRAKET_METHOD_NAMES = {
    "h": "h",
    "x": "x",
    "s": "s",
    "sdg": "si",
    "t": "t",
    "tdg": "ti",
    "ry": "ry",
    "rz": "rz",
    "cx": "cnot",
    "cu1": "cphaseshift",
    "swap": "swap",
    "ccx": "ccnot",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _spinq_counts(raw_counts: dict, circuit: Circuit) -> dict[str, int]:
    """Map SpinQit's q[0]..q[n-1] strings to c[n-1]..c[0]."""

    counts = Counter()
    for raw_key, count in raw_counts.items():
        if isinstance(raw_key, int):
            bits = format(raw_key, f"0{circuit.qubit_count}b")
        else:
            bits = str(raw_key).replace(" ", "")
        if len(bits) != circuit.qubit_count or set(bits) - {"0", "1"}:
            raise ValueError(f"Unsupported SpinQit count key: {raw_key!r}")

        classical = [0] * circuit.cbit_count
        for measurement in circuit.measurements:
            classical[measurement.cbit] = int(bits[measurement.qubit])
        key = "".join(str(bit) for bit in reversed(classical))
        counts[key] += int(count)
    return dict(sorted(counts.items()))


def _spinq_python() -> str:
    configured = os.environ.get("LOOMQ_SPINQIT_PYTHON")
    candidates = [configured] if configured else []
    candidates.extend(("/opt/loomq-spinqit/bin/python", sys.executable))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            if candidate != sys.executable:
                return candidate
            try:
                __import__("spinqit")
            except ImportError:
                continue
            return candidate
    raise RuntimeError(
        "spinqit==0.2.4 runtime is required for target: spinq; "
        "build with starter_kit/Dockerfile or set LOOMQ_SPINQIT_PYTHON"
    )


def run_spinq(circuit: Circuit, native_ir: str, shots: int) -> dict:
    """Execute the shared Circuit IR on SpinQit's Taurus local simulator."""

    python = _spinq_python()
    environment = os.environ.copy()
    site_package = Path(python).parent.parent / "lib/python3.10/site-packages/spinqit"
    for variable in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        current = environment.get(variable)
        environment[variable] = os.pathsep.join(
            value for value in (str(site_package), current) if value
        )

    payload = {
        "qubit_count": circuit.qubit_count,
        "shots": shots,
        "operations": [
            {
                "name": operation.name,
                "qubits": operation.qubits,
                "parameter": (
                    evaluate_angle(operation.parameter)
                    if operation.parameter is not None
                    else None
                ),
            }
            for operation in circuit.operations
        ],
    }
    worker = Path(__file__).with_name("spinqit_worker.py")
    try:
        completed = subprocess.run(
            [python, str(worker)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
            env=environment,
        )
        response = json.loads(completed.stdout)
    except subprocess.CalledProcessError as exc:
        reason = exc.stderr.strip() or exc.stdout.strip() or "unknown worker error"
        raise RuntimeError(f"SpinQit simulator failed: {reason}") from exc
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("SpinQit simulator returned an invalid result") from exc

    digest = hashlib.sha256(native_ir.encode("utf-8")).hexdigest()[:16]
    return {
        "backend": "spinq_taurus_simulator",
        "job_id": f"spinq-local-{digest}",
        "shots": shots,
        "counts": _spinq_counts(response["counts"], circuit),
        "bit_order": "little",
        "timestamp": _timestamp(),
        "meta": {
            "engine": "spinqit_basic_simulator",
            "sdk_version": response["sdk_version"],
            "transpiled_gates": len(circuit.operations),
        },
    }


def _origin_runtime_ir(circuit: Circuit) -> str:
    """Emit the concrete OriginIR dialect accepted by PyQPanda 3.8.5."""

    lines = [f"QINIT {circuit.qubit_count}", f"CREG {circuit.cbit_count}"]
    for operation in circuit.operations:
        operands = ", ".join(f"q[{qubit}]" for qubit in operation.qubits)
        if operation.name in ("sdg", "tdg"):
            lines.extend(
                ("DAGGER", f"{operation.name[0].upper()} {operands}", "ENDDAGGER")
            )
        elif operation.name in ("ry", "rz"):
            angle = format(evaluate_angle(operation.parameter or ""), ".17g")
            lines.append(f"{operation.name.upper()} {operands},({angle})")
        elif operation.name == "cu1":
            angle = format(evaluate_angle(operation.parameter or ""), ".17g")
            lines.append(f"CR {operands},({angle})")
        else:
            lines.append(f"{_ORIGIN_RUNTIME_NAMES[operation.name]} {operands}")
    lines.extend(
        f"MEASURE q[{measurement.qubit}], c[{measurement.cbit}]"
        for measurement in circuit.measurements
    )
    return "\n".join(lines) + "\n"


def _origin_counts(raw_counts: dict, width: int) -> dict[str, int]:
    counts = Counter()
    for raw_key, count in raw_counts.items():
        if isinstance(raw_key, int):
            key = format(raw_key, f"0{width}b")
        else:
            key = str(raw_key).replace(" ", "")
            if set(key) <= {"0", "1"}:
                key = key.zfill(width)
            elif key.isdecimal():
                key = format(int(key), f"0{width}b")
            else:
                raise ValueError(f"Unsupported PyQPanda count key: {raw_key!r}")
        if len(key) != width:
            raise ValueError(f"PyQPanda count width mismatch: {raw_key!r}")
        counts[key] += int(count)
    return dict(sorted(counts.items()))


def run_originq(circuit: Circuit, native_ir: str, shots: int) -> dict:
    """Execute on OriginQ's PyQPanda CPUQVM."""

    try:
        import pyqpanda as pq
    except ImportError as exc:
        raise RuntimeError("pyqpanda is required for target: originq") from exc

    machine = pq.CPUQVM()
    machine.init_qvm()
    try:
        program, _, cbits = pq.convert_originir_str_to_qprog(
            _origin_runtime_ir(circuit), machine
        )
        counts = _origin_counts(
            machine.run_with_configuration(program, cbits, shots),
            circuit.cbit_count,
        )
    finally:
        machine.finalize()

    digest = hashlib.sha256(native_ir.encode("utf-8")).hexdigest()[:16]
    return {
        "backend": "originq_local_simulator",
        "job_id": f"originq-local-{digest}",
        "shots": shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": _timestamp(),
        "meta": {
            "engine": "pyqpanda_cpuqvm",
            "sdk_version": version("pyqpanda"),
            "transpiled_gates": len(circuit.operations),
        },
    }


def _braket_counts(
    raw_counts: dict, measured_qubits: list[int], circuit: Circuit
) -> dict[str, int]:
    counts = Counter()
    for raw_key, count in raw_counts.items():
        bits = str(raw_key).replace(" ", "")
        if len(bits) != len(measured_qubits) or set(bits) - {"0", "1"}:
            raise ValueError(f"Unsupported Braket count key: {raw_key!r}")
        by_qubit = dict(zip(measured_qubits, map(int, bits)))
        classical = [0] * circuit.cbit_count
        for measurement in circuit.measurements:
            classical[measurement.cbit] = by_qubit[measurement.qubit]
        key = "".join(str(bit) for bit in reversed(classical))
        counts[key] += int(count)
    return dict(sorted(counts.items()))


def run_braket(circuit: Circuit, shots: int) -> dict:
    """Execute on AWS Braket's local state-vector simulator."""

    try:
        from braket.circuits import Circuit as BraketCircuit
        from braket.devices import LocalSimulator
    except ImportError as exc:
        raise RuntimeError("amazon-braket-sdk is required for target: braket") from exc

    program = BraketCircuit()
    for qubit in range(circuit.qubit_count):
        program.i(qubit)
    for operation in circuit.operations:
        method = getattr(program, _BRAKET_METHOD_NAMES[operation.name])
        arguments = list(operation.qubits)
        if operation.parameter is not None:
            arguments.append(evaluate_angle(operation.parameter))
        method(*arguments)

    result = LocalSimulator().run(program, shots=shots).result()
    return {
        "backend": "braket_local_simulator",
        "job_id": str(result.task_metadata.id),
        "shots": shots,
        "counts": _braket_counts(
            dict(result.measurement_counts), result.measured_qubits, circuit
        ),
        "bit_order": "little",
        "timestamp": _timestamp(),
        "meta": {
            "engine": "amazon_braket_local_simulator",
            "sdk_version": version("amazon-braket-sdk"),
            "transpiled_gates": len(circuit.operations),
        },
    }
