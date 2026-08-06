from importlib.util import find_spec
import unittest

from starter_kit import adapter
from starter_kit.evaluator import calculate_hellinger_fidelity, validate_schema


def qasm(body: str, qubits: int = 1) -> str:
    return f"""OPENQASM 2.0;
include "qelib1.inc";
qreg q[{qubits}];
creg c[{qubits}];
{body}
measure q -> c;
"""


def available_targets() -> tuple[str, ...]:
    targets = ["spinq"]
    if find_spec("pyqpanda") is not None:
        targets.append("originq")
    if find_spec("braket") is not None:
        targets.append("braket")
    return tuple(targets)


class SimulatorTests(unittest.TestCase):
    def test_supported_targets_pass_bell_schema_and_fidelity(self):
        expected_backends = {
            "spinq": "spinq_builtin_simulator",
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


if __name__ == "__main__":
    unittest.main()
