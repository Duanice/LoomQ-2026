#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

This file intentionally contains no scoring implementation. Teams may implement
the functions directly or delegate to another language/runtime with subprocess.
"""

from typing import Any, Dict, List, Tuple

try:
    from .emitters import emit_braket, emit_originq, emit_spinq
    from .platform_runners import run_braket, run_originq, run_spinq
    from .qasm_parser import Circuit, parse_qasm
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from emitters import emit_braket, emit_originq, emit_spinq
    from platform_runners import run_braket, run_originq, run_spinq
    from qasm_parser import Circuit, parse_qasm


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def _emit(circuit: Circuit, target: str) -> str:
    if target == "spinq":
        return emit_spinq(circuit)
    if target == "originq":
        return emit_originq(circuit)
    return emit_braket(circuit)


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    return _emit(parse_qasm(qasm_str), target)


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    if not isinstance(shots, int) or isinstance(shots, bool) or shots <= 0:
        raise ValueError("shots must be a positive integer")
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    circuit = parse_qasm(qasm_str)
    native_ir = _emit(circuit, target)
    if target == "spinq":
        return run_spinq(circuit, native_ir, shots)
    if target == "originq":
        return run_originq(circuit, native_ir, shots)
    if target == "braket":
        return run_braket(circuit, shots)


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
