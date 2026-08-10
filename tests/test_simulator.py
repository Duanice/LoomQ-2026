from importlib.util import find_spec
import json
import os
import unittest
from unittest import mock

from starter_kit import adapter
from starter_kit.evaluator import calculate_hellinger_fidelity, validate_schema
from starter_kit.platform_runners import _spinq_counts, run_spinq
from starter_kit.qasm_parser import Circuit, Measurement


def qasm(body: str, qubits: int = 1) -> str:
    return f"""OPENQASM 2.0;
include "qelib1.inc";
qreg q[{qubits}];
creg c[{qubits}];
{body}
measure q -> c;
"""


def available_targets() -> tuple[str, ...]:
    spinq_python = os.environ.get(
        "LOOMQ_SPINQIT_PYTHON", "/opt/loomq-spinqit/bin/python"
    )
    targets = []
    if os.path.isfile(spinq_python) or find_spec("spinqit") is not None:
        targets.append("spinq")
    if find_spec("pyqpanda") is not None:
        targets.append("originq")
    if find_spec("braket") is not None:
        targets.append("braket")
    return tuple(targets)


class SimulatorTests(unittest.TestCase):
    def test_supported_targets_pass_bell_schema_and_fidelity(self):
        expected_backends = {
            "spinq": "spinq_taurus_simulator",
            "originq": "originq_local_simulator",
            "braket": "braket_local_simulator",
        }
        for target in available_targets():
            with self.subTest(target=target):
                result = adapter.run(
                    qasm("h q[0];\ncx q[0],q[1];", 2), target, 8192
                )
                self.assertEqual(validate_schema(result), (True, "schema valid"))
                self.assertEqual(result["backend"], expected_backends[target])
                observed = {
                    key: value / 8192 for key, value in result["counts"].items()
                }
                self.assertGreaterEqual(
                    calculate_hellinger_fidelity(
                        observed, {"00": 0.5, "11": 0.5}
                    ),
                    0.97,
                )

    def test_phase_and_rotation_gates(self):
        for target in available_targets():
            with self.subTest(target=target):
                phase = adapter.run(
                    qasm(
                        "h q[0]; s q[0]; sdg q[0]; t q[0]; tdg q[0]; "
                        "rz(pi) q[0]; h q[0];"
                    ),
                    target,
                    128,
                )
                rotation = adapter.run(qasm("ry(pi) q[0];"), target, 128)
                self.assertEqual(phase["counts"], {"1": 128})
                self.assertEqual(rotation["counts"], {"1": 128})

    def test_controlled_and_swap_gates(self):
        for target in available_targets():
            with self.subTest(target=target):
                cu1 = adapter.run(
                    qasm("h q[0]; h q[1]; cu1(pi) q[0],q[1]; h q[1];", 2),
                    target,
                    8192,
                )
                swap = adapter.run(
                    qasm("x q[0]; swap q[0],q[1];", 2), target, 128
                )
                ccx = adapter.run(
                    qasm("x q[0]; x q[1]; ccx q[0],q[1],q[2];", 3),
                    target,
                    128,
                )
                self.assertEqual(set(cu1["counts"]), {"00", "11"})
                self.assertEqual(swap["counts"], {"10": 128})
                self.assertEqual(ccx["counts"], {"111": 128})

    def test_platform_runners_normalize_asymmetric_bit_order(self):
        for target in available_targets():
            with self.subTest(target=target):
                result = adapter.run(qasm("x q[0];", 2), target, 128)
                self.assertEqual(result["counts"], {"01": 128})

                remapped = adapter.run(
                    """OPENQASM 2.0;
                    include "qelib1.inc";
                    qreg q[2]; creg c[2];
                    x q[0];
                    measure q[0] -> c[1];
                    measure q[1] -> c[0];
                    """,
                    target,
                    128,
                )
                self.assertEqual(remapped["counts"], {"10": 128})

    def test_rejects_invalid_shots(self):
        with self.assertRaisesRegex(ValueError, "shots must be a positive integer"):
            adapter.run(qasm("x q[0];"), "spinq", 0)

    def test_spinq_counts_are_mapped_to_classical_little_endian_order(self):
        circuit = Circuit(
            qubit_count=2,
            cbit_count=2,
            operations=(),
            measurements=(Measurement(0, 1), Measurement(1, 0)),
        )
        self.assertEqual(_spinq_counts({"10": 128}, circuit), {"10": 128})

    @mock.patch("starter_kit.platform_runners._spinq_python")
    @mock.patch("starter_kit.platform_runners.subprocess.run")
    def test_spinq_worker_receives_native_library_paths(self, execute, python):
        python.return_value = "/tmp/spinq-runtime/bin/python"
        execute.return_value.stdout = json.dumps(
            {"counts": {"0": 8}, "sdk_version": "0.2.4"}
        )
        circuit = Circuit(1, 1, (), (Measurement(0, 0),))

        with mock.patch.dict(
            os.environ,
            {"DYLD_LIBRARY_PATH": "/existing/dyld", "LD_LIBRARY_PATH": "/existing/ld"},
        ):
            run_spinq(circuit, "native-ir", 8)

        environment = execute.call_args.kwargs["env"]
        expected = "/tmp/spinq-runtime/lib/python3.10/site-packages/spinqit"
        self.assertEqual(
            environment["DYLD_LIBRARY_PATH"], f"{expected}{os.pathsep}/existing/dyld"
        )
        self.assertEqual(
            environment["LD_LIBRARY_PATH"], f"{expected}{os.pathsep}/existing/ld"
        )


if __name__ == "__main__":
    unittest.main()
