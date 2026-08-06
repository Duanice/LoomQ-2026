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


class SimulatorTests(unittest.TestCase):
    def test_spinq_bell_passes_schema_and_fidelity(self):
        result = adapter.run(qasm("h q[0];\ncx q[0],q[1];", 2), "spinq", 8192)
        self.assertEqual(validate_schema(result), (True, "schema valid"))
        observed = {key: value / 8192 for key, value in result["counts"].items()}
        self.assertGreaterEqual(
            calculate_hellinger_fidelity(observed, {"00": 0.5, "11": 0.5}),
            0.97,
        )

    def test_phase_and_rotation_gates(self):
        phase = adapter.run(
            qasm("h q[0]; s q[0]; sdg q[0]; t q[0]; tdg q[0]; rz(pi) q[0]; h q[0];"),
            "spinq",
            128,
        )
        rotation = adapter.run(qasm("ry(pi) q[0];"), "spinq", 128)
        self.assertEqual(phase["counts"], {"1": 128})
        self.assertEqual(rotation["counts"], {"1": 128})

    def test_controlled_and_swap_gates(self):
        cu1 = adapter.run(
            qasm("h q[0]; h q[1]; cu1(pi) q[0],q[1]; h q[1];", 2),
            "spinq",
            8192,
        )
        swap = adapter.run(qasm("x q[0]; swap q[0],q[1];", 2), "spinq", 128)
        ccx = adapter.run(
            qasm("x q[0]; x q[1]; ccx q[0],q[1],q[2];", 3),
            "spinq",
            128,
        )
        self.assertEqual(set(cu1["counts"]), {"00", "11"})
        self.assertEqual(swap["counts"], {"10": 128})
        self.assertEqual(ccx["counts"], {"111": 128})

    def test_rejects_invalid_shots(self):
        with self.assertRaisesRegex(ValueError, "shots must be a positive integer"):
            adapter.run(qasm("x q[0];"), "spinq", 0)


if __name__ == "__main__":
    unittest.main()
