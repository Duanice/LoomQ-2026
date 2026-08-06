import unittest

from starter_kit import adapter
from starter_kit.emitters import emit_spinq
from starter_kit.qasm_parser import Circuit, Measurement, Operation, parse_qasm


BELL_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
"""


class QasmParserTests(unittest.TestCase):
    def test_parses_bell_circuit(self):
        self.assertEqual(
            parse_qasm(BELL_QASM),
            Circuit(
                qubit_count=2,
                cbit_count=2,
                operations=(Operation("h", (0,)), Operation("cx", (0, 1))),
                measurements=(Measurement(0, 0), Measurement(1, 1)),
            ),
        )

    def test_parses_parameters_comments_and_individual_measurements(self):
        circuit = parse_qasm(
            """OPENQASM 2.0;
            include "qelib1.inc";
            qreg qubits[3];
            creg bits[3];
            // Keep the exact angle expression for later emitters.
            ry(pi/2) qubits[0];
            ccx qubits[0], qubits[1], qubits[2];
            measure qubits[2] -> bits[0];
            """
        )
        self.assertEqual(
            circuit.operations,
            (Operation("ry", (0,), "pi/2"), Operation("ccx", (0, 1, 2))),
        )
        self.assertEqual(circuit.measurements, (Measurement(2, 0),))

    def test_rejects_out_of_range_qubit(self):
        with self.assertRaisesRegex(ValueError, "量子比特越界"):
            parse_qasm(BELL_QASM.replace("h q[0]", "h q[2]"))

    def test_parses_all_twelve_allowed_gates(self):
        circuit = parse_qasm(
            """OPENQASM 2.0;
            include "qelib1.inc";
            qreg q[3];
            creg c[3];
            h q[0]; x q[0]; s q[0]; sdg q[0]; t q[0]; tdg q[0];
            rz(pi/2) q[0]; ry(-pi/4) q[0];
            cx q[0],q[1]; cu1(pi/3) q[0],q[1]; swap q[1],q[2];
            ccx q[0],q[1],q[2];
            measure q -> c;
            """
        )
        self.assertEqual(
            tuple(operation.name for operation in circuit.operations),
            ("h", "x", "s", "sdg", "t", "tdg", "rz", "ry", "cx", "cu1", "swap", "ccx"),
        )

    def test_spinq_emitter_round_trips_through_parser(self):
        circuit = parse_qasm(BELL_QASM)
        emitted = emit_spinq(circuit)
        self.assertEqual(parse_qasm(emitted), circuit)
        self.assertEqual(adapter.transpile(BELL_QASM, "spinq"), emitted)


if __name__ == "__main__":
    unittest.main()
