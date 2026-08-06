#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

This file intentionally contains no scoring implementation. Teams may implement
the functions directly or delegate to another language/runtime with subprocess.
"""

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Tuple

try:
    from .emitters import emit_braket, emit_originq, emit_spinq
    from .qasm_parser import parse_qasm
    from .simulator import sample_counts
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from emitters import emit_braket, emit_originq, emit_spinq
    from qasm_parser import parse_qasm
    from simulator import sample_counts


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    circuit = parse_qasm(qasm_str)
    if target == "spinq":
        return emit_spinq(circuit)
    if target == "originq":
        return emit_originq(circuit)
    return emit_braket(circuit)


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    if not isinstance(shots, int) or isinstance(shots, bool) or shots <= 0:
        raise ValueError("shots must be a positive integer")
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    native_ir = transpile(qasm_str, target)
    circuit = parse_qasm(qasm_str)
    digest = hashlib.sha256(native_ir.encode("utf-8")).hexdigest()[:16]
    seed = int(digest, 16) ^ shots
    return {
        "backend": f"{target}_builtin_simulator",
        "job_id": f"{target}-local-{digest}",
        "shots": shots,
        "counts": sample_counts(circuit, shots, seed),
        "bit_order": "little",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "meta": {
            "engine": "builtin_statevector",
            "transpiled_gates": len(circuit.operations),
        },
    }


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
