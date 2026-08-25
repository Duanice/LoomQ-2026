#!/usr/bin/env python3
"""Isolated SpinQit 0.2.4 worker; reads a serialized shared Circuit from stdin."""

from contextlib import redirect_stdout
import json
from importlib.metadata import version
import sys

from spinqit import (
    BasicSimulatorConfig,
    CCX,
    CX,
    Circuit,
    H,
    P,
    Ry,
    Rz,
    S,
    Sd,
    SWAP,
    T,
    Td,
    X,
    get_basic_simulator,
    get_compiler,
    get_spinq_cloud,
    SpinQCloudConfig,
)


GATES = {
    "h": H,
    "x": X,
    "s": S,
    "sdg": Sd,
    "t": T,
    "tdg": Td,
    "ry": Ry,
    "rz": Rz,
    "cx": CX,
    "swap": SWAP,
    "ccx": CCX,
}


def build_circuit(payload: dict) -> Circuit:
    circuit = Circuit()
    register = circuit.allocateQubits(payload["qubit_count"])

    for operation in payload["operations"]:
        name = operation["name"]
        qubits = [register[index] for index in operation["qubits"]]
        angle = operation["parameter"]
        if name == "cu1":
            # qelib1.inc: cu1(theta) decomposition from gate_identities.md.
            circuit << (P, qubits[0], angle / 2)
            circuit << (CX, qubits)
            circuit << (P, qubits[1], -angle / 2)
            circuit << (CX, qubits)
            circuit << (P, qubits[1], angle / 2)
        elif angle is None:
            circuit << (GATES[name], qubits)
        else:
            circuit << (GATES[name], qubits, angle)

    return circuit


def execute(payload: dict) -> dict:
    circuit = build_circuit(payload)

    executable = get_compiler("native").compile(circuit, 0)
    config = BasicSimulatorConfig()
    config.configure_shots(payload["shots"])
    result = get_basic_simulator().execute(executable, config)
    return {
        "counts": {str(key): int(value) for key, value in result.counts.items()},
        "sdk_version": version("spinqit"),
    }


def cloud(payload: dict) -> dict:
    backend = get_spinq_cloud(payload["username"], payload["keyfile"])
    action = payload["action"]

    if action == "preflight":
        return {
            "platforms": [
                {
                    "code": platform.code,
                    "name": platform.name,
                    "max_qubits": platform.max_bitnum,
                    "machine_count": platform.machine_count,
                    "simulator": platform.simu,
                }
                for platform in backend.platforms
            ],
            "sdk_version": version("spinqit"),
        }

    if action == "query":
        result = backend.get_task_result(
            payload["job_id"], timeout=payload.get("timeout", 3600)
        )
        raw_result = backend._get_task_result(payload["job_id"])
        return {
            "job_id": result.task_code,
            "shots": result._shots,
            "counts": result.counts,
            "probabilities": result.probabilities,
            "raw_result": raw_result,
            "sdk_version": version("spinqit"),
        }

    circuit = build_circuit(payload)
    executable = get_compiler("native").compile(circuit, 0)
    config = SpinQCloudConfig()
    config.configure_platform(payload["platform"])
    config.configure_shots(payload["shots"])
    config.configure_task(payload["task_name"], payload["task_description"])
    status, message, task_code = backend.submit_task(executable, config)
    return {
        "job_id": task_code,
        "status": status,
        "message": message,
        "platform": payload["platform"],
        "shots": payload["shots"],
        "sdk_version": version("spinqit"),
    }


if __name__ == "__main__":
    request = json.load(sys.stdin)
    # SpinQit prints task progress to stdout. Keep stdout machine-readable.
    with redirect_stdout(sys.stderr):
        response = cloud(request) if request.get("cloud") else execute(request)
    json.dump(response, sys.stdout, separators=(",", ":"))
