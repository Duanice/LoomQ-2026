#!/usr/bin/env python3
"""Submit one shared-IR circuit to SpinQ or OriginQ hardware and save evidence."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from .platform_runners import _origin_counts, _spinq_counts
    from .qasm_parser import Circuit, parse_qasm
    from .simulator import evaluate_angle
except ImportError:
    from platform_runners import _origin_counts, _spinq_counts
    from qasm_parser import Circuit, parse_qasm
    from simulator import evaluate_angle


ROOT = Path(__file__).resolve().parent
DEFAULT_QASM = ROOT / "circuits" / "bell.qasm"
EVIDENCE = ROOT / "evidence" / "files"
ORIGIN_PRIORITY = ("72", "WK_C180", "WK_C102-2", "WK_C102_400")
SECRET_KEYS = ("api_key", "apikey", "authorization", "signature", "token", "username")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _circuit_payload(circuit: Circuit) -> dict:
    return {
        "qubit_count": circuit.qubit_count,
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
        "measurements": [
            {"qubit": item.qubit, "cbit": item.cbit}
            for item in circuit.measurements
        ],
    }


def _redact(value):
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if any(secret in key.lower() for secret in SECRET_KEYS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _worker(target: str, payload: dict, secret: str | None = None) -> dict:
    if target == "spinq":
        python = os.environ.get(
            "LOOMQ_SPINQIT_PYTHON",
            str(Path.home() / ".cache/loomq/spinqit-0.2.4/bin/python"),
        )
        script = ROOT / "spinqit_worker.py"
        environment = os.environ.copy()
        site_package = Path(python).parent.parent / "lib/python3.10/site-packages/spinqit"
        environment["DYLD_LIBRARY_PATH"] = str(site_package)
        no_proxy = environment.get("NO_PROXY", environment.get("no_proxy", ""))
        bypass = [item for item in no_proxy.split(",") if item]
        if "cloud.spinq.cn" not in bypass:
            bypass.append("cloud.spinq.cn")
        environment["NO_PROXY"] = environment["no_proxy"] = ",".join(bypass)
    else:
        python = os.environ.get(
            "LOOMQ_ORIGINQ_PYTHON",
            str(Path.home() / ".cache/loomq/pyqpanda3-0.4.0/bin/python"),
        )
        script = ROOT / "originq_cloud_worker.py"
        environment = os.environ.copy()

    if not Path(python).is_file():
        raise RuntimeError(f"Hardware SDK Python not found: {python}")
    try:
        completed = subprocess.run(
            [python, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            timeout=payload.get("timeout", 120) + 60,
            env=environment,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "worker failed"
        if secret:
            message = message.replace(secret, "<redacted>")
        raise RuntimeError(message) from exc
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid {target} worker response") from exc


def _origin_key() -> str:
    for name in ("LOOMQ_ORIGINQ_API_KEY", "ORIGINQ_API_KEY", "ORIGINQ_API_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    if sys.stdin.isatty():
        return getpass.getpass("OriginQ API Key: ")
    raise RuntimeError(
        "Set LOOMQ_ORIGINQ_API_KEY or run this command in an interactive terminal"
    )


def _select_platform(target: str, preflight: dict, requested: str | None) -> str:
    if target == "spinq":
        selected = requested or "gemini_vp"
        matches = [item for item in preflight["platforms"] if item["code"] == selected]
        if not matches:
            raise RuntimeError(f"SpinQ platform is unavailable or denied: {selected}")
        platform = matches[0]
        if platform["simulator"] or platform["machine_count"] <= 0:
            raise RuntimeError(f"SpinQ platform has no online real machine: {selected}")
        return selected

    backends = preflight["backends"]
    selected = requested or next(
        (name for name in ORIGIN_PRIORITY if backends.get(name)), None
    )
    if not selected or not backends.get(selected):
        raise RuntimeError("No requested OriginQ real backend is currently available")
    if "amplitude" in selected.lower():
        raise RuntimeError(f"OriginQ backend is a simulator, not hardware: {selected}")
    return selected


def _verify_bell(counts: dict[str, int], shots: int) -> list[str]:
    if sum(counts.values()) != shots:
        raise RuntimeError(f"Counts total {sum(counts.values())} does not equal {shots}")
    peaks = sorted(counts, key=lambda key: (-counts[key], key))[:2]
    if set(peaks) != {"00", "11"}:
        raise RuntimeError(f"Bell main peaks missed: {peaks}")
    return peaks


def _counts_from_probabilities(probabilities: dict, shots: int) -> dict[str, int]:
    if not probabilities:
        return {}
    total = sum(float(value) for value in probabilities.values())
    if total <= 0:
        return {}
    exact = {
        str(key): float(value) / total * shots
        for key, value in probabilities.items()
    }
    counts = {key: int(value) for key, value in exact.items()}
    missing = shots - sum(counts.values())
    order = sorted(exact, key=lambda key: (exact[key] - counts[key], key), reverse=True)
    for key in order[:missing]:
        counts[key] += 1
    return counts


def run(args: argparse.Namespace) -> dict:
    qasm = args.qasm.read_text(encoding="utf-8")
    circuit = parse_qasm(qasm)
    payload = _circuit_payload(circuit)
    payload.update({"action": "preflight", "timeout": 120})

    if args.target == "spinq":
        secret = None
        payload.update(
            {
                "cloud": True,
                "username": args.username,
                "keyfile": str(args.keyfile.expanduser()),
            }
        )
        if not args.username:
            raise RuntimeError("SpinQ username is required")
    else:
        secret = _origin_key()
        payload["api_key"] = secret

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    submission_path = EVIDENCE / f"{args.target}-submission.json"
    resume = args.submit and submission_path.exists() and not args.force_new
    if resume:
        submitted = json.loads(submission_path.read_text(encoding="utf-8"))
        job_id = submitted["job_id"]
        platform = submitted["platform"]
    else:
        preflight = _worker(args.target, payload, secret)
        platform = _select_platform(args.target, preflight, args.platform)
        if not args.submit:
            return {
                "target": args.target,
                "selected": platform,
                "preflight": preflight,
            }
        payload.update(
            {
                "action": "submit",
                "platform": platform,
                "shots": args.shots,
                "task_name": f"LoomQ-2026-Bell-{args.target}",
                "task_description": "LoomQ 2026 hardware evidence: Bell state",
            }
        )
        submitted = _worker(args.target, payload, secret)
        job_id = submitted.get("job_id")
        if not job_id:
            raise RuntimeError(f"{args.target} did not return a traceable job ID")
        submission_path.write_text(
            json.dumps(_redact(submitted), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    payload.update({"action": "query", "job_id": job_id, "timeout": args.timeout})
    response = _worker(args.target, payload, secret)
    raw_path = EVIDENCE / f"{args.target}-raw-result.json"
    raw_path.write_text(
        json.dumps(_redact(response["raw_result"]), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    raw_counts = response["counts"] or _counts_from_probabilities(
        response.get("probabilities", {}), args.shots
    )
    counts = (
        _spinq_counts(raw_counts, circuit)
        if args.target == "spinq"
        else _origin_counts(raw_counts, circuit.cbit_count)
    )
    peaks = _verify_bell(counts, args.shots)
    result = {
        "backend": "spinq_cloud_qpu" if args.target == "spinq" else "originq_wukong",
        "job_id": job_id,
        "shots": args.shots,
        "counts": counts,
        "bit_order": "little",
        "timestamp": _timestamp(),
        "meta": {
            "is_mock": False,
            "platform": platform,
            "sdk_version": response["sdk_version"],
            "main_peaks": peaks,
            "main_peak_hit": True,
        },
    }
    (EVIDENCE / f"{args.target}-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--target", choices=("spinq", "originq"), required=True)
    value.add_argument("--submit", action="store_true", help="consume quota and submit")
    value.add_argument(
        "--force-new",
        action="store_true",
        help="submit a new job even when a saved submission receipt exists",
    )
    value.add_argument("--shots", type=int, default=1024)
    value.add_argument("--timeout", type=int, default=3600)
    value.add_argument("--qasm", type=Path, default=DEFAULT_QASM)
    value.add_argument("--platform")
    value.add_argument("--username", default=os.environ.get("LOOMQ_SPINQ_USERNAME"))
    value.add_argument(
        "--keyfile", type=Path, default=Path.home() / ".ssh/loomq_spinq"
    )
    return value


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.shots <= 0 or args.timeout <= 0:
        raise SystemExit("shots and timeout must be positive")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
