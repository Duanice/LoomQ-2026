#!/usr/bin/env python3
"""Preflight or submit the Bell circuit to real SpinQ Cloud hardware."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_runner import main


if __name__ == "__main__":
    main(["--target", "spinq", *sys.argv[1:]])
