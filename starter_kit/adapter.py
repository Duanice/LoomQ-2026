#!/usr/bin/env python3
"""LoomQ submission adapter contract v1.0.

This file intentionally contains no scoring implementation. Teams may implement
the functions directly or delegate to another language/runtime with subprocess.
"""

from typing import Any, Dict, List, Tuple

try:
    from .emitters import emit_spinq
    from .qasm_parser import parse_qasm
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from emitters import emit_spinq
    from qasm_parser import parse_qasm


SUPPORTED_TARGETS = ("spinq", "originq", "braket")


def transpile(qasm_str: str, target: str) -> str:
    """Translate OpenQASM 2.0 into the target backend's native representation."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    circuit = parse_qasm(qasm_str)
    if target == "spinq":
        return emit_spinq(circuit)
    raise NotImplementedError(f"Emitter not implemented for target: {target}")


def run(qasm_str: str, target: str, shots: int) -> Dict[str, Any]:
    """Execute a circuit and return the unified result schema from the rules."""
    raise NotImplementedError("Implement run(qasm_str, target, shots)")


def agent_chat(prompt: str) -> str:
    """Optional L2 entry point using the documented LOOMQ_LLM_* environment."""
    raise NotImplementedError("L2 is optional; implement agent_chat(prompt) to enter")


def compile_hybrid(hybrid_qasm_str: str) -> Tuple[List[str], str]:
    """Optional L3 entry point. Return quantum operations and RISC-V assembly."""
    raise NotImplementedError(
        "L3 is optional; implement compile_hybrid(hybrid_qasm_str) to enter"
    )
