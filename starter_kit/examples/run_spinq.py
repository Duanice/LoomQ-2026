#!/usr/bin/env python3
"""Run a Bell circuit on the real SpinQit Taurus local simulator driver."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapter import run


BELL_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q -> c;
"""


if __name__ == "__main__":
    print(json.dumps(run(BELL_QASM, "spinq", 1024), ensure_ascii=False, indent=2))
