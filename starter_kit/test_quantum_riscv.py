"""End-to-end tests for the executable LoomQ quantum RISC-V extension."""

import math
import unittest

try:
    from .qasm_parser import parse_qasm
    from .riscv_emulator import (
        ANGLE_SCALE,
        QUANTUM_OPCODE,
        TinyRISCVEmulator,
        decode_quantum_instruction,
        encode_quantum_circuit,
        encode_quantum_instruction,
        run_quantum_circuit,
    )
    from .simulator import simulate
except ImportError:  # Support direct execution from starter_kit/.
    from qasm_parser import parse_qasm
    from riscv_emulator import (
        ANGLE_SCALE,
        QUANTUM_OPCODE,
        TinyRISCVEmulator,
        decode_quantum_instruction,
        encode_quantum_circuit,
        encode_quantum_instruction,
        run_quantum_circuit,
    )
    from simulator import simulate


def qasm(body: str, qubits: int) -> str:
    return f'''OPENQASM 2.0;
include "qelib1.inc";
qreg q[{qubits}];
creg c[{qubits}];
{body}
'''


class QuantumRISCVE2ETests(unittest.TestCase):
    def test_known_machine_word_and_all_gate_round_trips(self):
        self.assertEqual(encode_quantum_instruction("h", (3,)), 0x0200018B)
        cases = [
            ("h", (0,), None),
            ("x", (1,), None),
            ("s", (2,), None),
            ("sdg", (3,), None),
            ("t", (4,), None),
            ("tdg", (5,), None),
            ("ry", (6,), math.pi / 7),
            ("rz", (7,), -math.pi / 5),
            ("cx", (1, 8), None),
            ("cu1", (2, 9), math.pi / 3),
            ("swap", (3, 10), None),
            ("ccx", (4, 5, 11), None),
        ]
        for name, qubits, angle in cases:
            with self.subTest(name=name):
                decoded = decode_quantum_instruction(
                    encode_quantum_instruction(name, qubits, angle)
                )
                self.assertEqual((decoded.name, decoded.qubits), (name, qubits))
                if angle is not None:
                    self.assertLessEqual(abs(decoded.parameter - angle), ANGLE_SCALE / 2)

    def test_shared_ir_encodes_every_word_with_custom_opcode(self):
        circuit = parse_qasm(
            qasm(
                """h q[0]; x q[1]; s q[2]; sdg q[0]; t q[1]; tdg q[2];
                ry(pi/7) q[0]; rz(-pi/5) q[1]; cx q[0],q[1];
                cu1(pi/3) q[1],q[2]; swap q[0],q[2]; ccx q[0],q[1],q[2];
                measure q -> c;""",
                3,
            )
        )
        words = encode_quantum_circuit(circuit)

        self.assertEqual(len(words), 15)
        self.assertTrue(all(word & 0x7F == QUANTUM_OPCODE for word in words))
        self.assertEqual(
            [decode_quantum_instruction(word).name for word in words],
            [
                "h",
                "x",
                "s",
                "sdg",
                "t",
                "tdg",
                "ry",
                "rz",
                "cx",
                "cu1",
                "swap",
                "ccx",
                "measure",
                "measure",
                "measure",
            ],
        )
        emulator = TinyRISCVEmulator(seed=7)
        emulator.load_machine_program(words[:12], circuit.qubit_count)
        emulator.execute()
        overlap = sum(
            expected.conjugate() * actual
            for expected, actual in zip(simulate(circuit), emulator.quantum_state)
        )
        self.assertGreater(abs(overlap) ** 2, 0.99999)
        self.assertEqual(sum(run_quantum_circuit(circuit, 64, seed=7).values()), 64)

    def test_bell_and_ghz_execute_from_raw_machine_words(self):
        bell = parse_qasm(qasm("h q[0]; cx q[0],q[1]; measure q -> c;", 2))
        ghz = parse_qasm(
            qasm(
                "h q[0]; cx q[0],q[1]; cx q[1],q[2]; measure q -> c;", 3
            )
        )

        bell_counts = run_quantum_circuit(bell, 1024, seed=2026)
        ghz_counts = run_quantum_circuit(ghz, 1024, seed=2026)

        self.assertEqual(set(bell_counts), {"00", "11"})
        self.assertEqual(set(ghz_counts), {"000", "111"})
        self.assertGreater(bell_counts["00"], 400)
        self.assertGreater(bell_counts["11"], 400)
        self.assertGreater(ghz_counts["000"], 400)
        self.assertGreater(ghz_counts["111"], 400)

    def test_quantum_words_mix_with_classical_riscv(self):
        program = """
        qh q0
        qcx q0, q1
        qmeasure q0, x10
        qmeasure q1, x11
        add x12, x10, x11
        """
        outcomes = set()
        for seed in range(32):
            emulator = TinyRISCVEmulator(seed)
            emulator.load_program(program, qubit_count=2)
            state = emulator.execute()
            self.assertEqual(state.get("x10", 0), state.get("x11", 0))
            self.assertEqual(state.get("x12", 0), 2 * state.get("x10", 0))
            outcomes.add(state.get("x10", 0))
        self.assertEqual(outcomes, {0, 1})

    def test_parameter_mnemonic_accepts_readable_angle_expression(self):
        emulator = TinyRISCVEmulator(seed=7)
        emulator.load_program("qry q0, pi / 2\nqmeasure q0, x10", qubit_count=1)
        self.assertIn(emulator.execute().get("x10", 0), (0, 1))

    def test_measurement_collapses_before_second_read(self):
        program = """
        qh q0
        qmeasure q0, x10
        qmeasure q0, x11
        """
        for seed in range(32):
            emulator = TinyRISCVEmulator(seed)
            emulator.load_program(program, qubit_count=1)
            state = emulator.execute()
            self.assertEqual(state.get("x10", 0), state.get("x11", 0))

    def test_rejects_non_custom_reserved_and_malformed_words(self):
        with self.assertRaisesRegex(ValueError, "不是 LoomQ"):
            decode_quantum_instruction(0x00000013)  # Standard RISC-V ADDI.
        with self.assertRaisesRegex(ValueError, "保留的量子 funct3"):
            decode_quantum_instruction((0x4 << 12) | QUANTUM_OPCODE)
        malformed_h = encode_quantum_instruction("h", (0,)) | (1 << 15)
        with self.assertRaisesRegex(ValueError, "保留字段"):
            decode_quantum_instruction(malformed_h)
        with self.assertRaisesRegex(ValueError, "不能重复"):
            encode_quantum_instruction("cx", (1, 1))
        with self.assertRaisesRegex(ValueError, "有限角度"):
            encode_quantum_instruction("ry", (0,), math.inf)


if __name__ == "__main__":
    unittest.main()
