#!/usr/bin/env python3
"""Isolated pyqpanda3 cloud worker; JSON in and JSON out."""

import json
import sys
import time

from pyqpanda3.core import CNOT, CP, H, QProg, RY, RZ, S, SWAP, T, TOFFOLI, X, measure
from pyqpanda3.qcloud import JobStatus, QCloudJob, QCloudOptions, QCloudService


def build_program(payload: dict) -> QProg:
    program = QProg()
    gates = {
        "h": H,
        "x": X,
        "s": S,
        "t": T,
        "ry": RY,
        "rz": RZ,
        "cx": CNOT,
        "cu1": CP,
        "swap": SWAP,
        "ccx": TOFFOLI,
    }
    for operation in payload["operations"]:
        name = operation["name"]
        arguments = list(operation["qubits"])
        if operation["parameter"] is not None:
            arguments.append(operation["parameter"])
        if name in ("sdg", "tdg"):
            gate = (S if name == "sdg" else T)(*arguments).dagger()
        else:
            gate = gates[name](*arguments)
        program << gate
    for item in payload["measurements"]:
        program << measure(item["qubit"], item["cbit"])
    return program


def execute(payload: dict) -> dict:
    service = QCloudService(api_key=payload["api_key"])
    action = payload["action"]
    if action == "preflight":
        return {"backends": service.backends(), "sdk_version": "0.4.0"}

    if action == "query":
        job = QCloudJob(payload["job_id"])
        deadline = time.monotonic() + payload.get("timeout", 3600)
        status = job.status()
        while status not in (JobStatus.FINISHED, JobStatus.FAILED):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for OriginQ job {payload['job_id']}")
            time.sleep(15)
            status = job.status()
        if status == JobStatus.FAILED:
            result = job.query()
            raise RuntimeError(result.error_message() or "OriginQ job failed")
        result = job.result()
        counts = result.get_counts()
        probabilities = result.get_probs()
        if not counts:
            count_list = result.get_counts_list()
            counts = count_list[0] if count_list else {}
        if not probabilities:
            probability_list = result.get_probs_list()
            probabilities = probability_list[0] if probability_list else {}
        raw = result.origin_data()
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            pass
        return {
            "job_id": result.job_id(),
            "counts": counts,
            "probabilities": probabilities,
            "raw_result": raw,
            "timing": result.timing_info(),
            "sdk_version": "0.4.0",
        }

    options = QCloudOptions()
    options.set_amend(True)
    options.set_mapping(True)
    options.set_optimization(True)
    backend = service.backend(payload["platform"])
    job = backend.run(build_program(payload), payload["shots"], options)
    return {
        "job_id": job.job_id(),
        "status": job.status().name,
        "platform": payload["platform"],
        "shots": payload["shots"],
        "sdk_version": "0.4.0",
    }


if __name__ == "__main__":
    json.dump(execute(json.load(sys.stdin)), sys.stdout, separators=(",", ":"))
