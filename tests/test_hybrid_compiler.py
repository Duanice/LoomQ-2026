import itertools
import random
import unittest

from starter_kit import adapter
from starter_kit.l3_defense import run_defense
from starter_kit.riscv_emulator import TinyRISCVEmulator


def program(classical: str, cbits: int = 1, before: str = "", after: str = "") -> str:
    return f'''OPENQASM 2.0;
include "qelib1.inc";
qreg q[{cbits}];
creg c[{cbits}];
{before}
classical {{ {classical} }}
{after}
'''


def execute(assembly: str, measured: tuple[int, ...]) -> dict[str, int]:
    emulator = TinyRISCVEmulator()
    emulator.load_program(assembly)
    for index, value in enumerate(measured):
        emulator.set_register(f"x{10 + index}", value)
    return emulator.execute()


class HybridCompilerTests(unittest.TestCase):
    def test_public_branch_and_instruction_whitelist(self):
        source = program(
            "if (c[0] == 1) { r1 = 7; } else { r1 = 3; }",
            before="measure q[0] -> c[0];",
        )

        quantum, assembly = adapter.compile_hybrid(source)

        self.assertEqual(quantum, ["measure q[0] -> c[0];"])
        self.assertEqual(execute(assembly, (0,)).get("x1"), 3)
        self.assertEqual(execute(assembly, (1,)).get("x1"), 7)
        allowed = {"li", "add", "sub", "addi", "beq", "bne", "j"}
        instructions = {
            line.split()[0]
            for line in assembly.splitlines()
            if line.strip() and not line.endswith(":")
        }
        self.assertLessEqual(instructions, allowed)

    def test_preserves_quantum_operations_around_classical_block(self):
        source = program(
            "r1 = 1;",
            cbits=2,
            before="h q[0]; measure q[0] -> c[0];",
            after="cx q[0], q[1]; measure q[1] -> c[1];",
        )

        quantum, _ = adapter.compile_hybrid(source)

        self.assertEqual(
            quantum,
            [
                "h q[0];",
                "measure q[0] -> c[0];",
                "cx q[0], q[1];",
                "measure q[1] -> c[1];",
            ],
        )

    def test_preserves_register_measurement_as_one_quantum_statement(self):
        source = program(
            "r1 = c[0] + c[1];",
            cbits=2,
            before="h q[0]; measure q -> c;",
            after="cx q[0], q[1];",
        )

        quantum, assembly = adapter.compile_hybrid(source)

        self.assertEqual(
            quantum,
            ["h q[0];", "measure q -> c;", "cx q[0], q[1];"],
        )
        self.assertEqual(execute(assembly, (1, 1)).get("x1"), 2)

    def test_assignment_does_not_clobber_source_registers(self):
        source = program("r1 = 5; r2 = 7; r1 = r2 + r1; r3 = (r1 - 2) + c[0];")
        _, assembly = adapter.compile_hybrid(source)

        for measured in (0, 1):
            with self.subTest(measured=measured):
                state = execute(assembly, (measured,))
                self.assertEqual(state.get("x1"), 12)
                self.assertEqual(state.get("x2"), 7)
                self.assertEqual(state.get("x3"), 10 + measured)
                self.assertFalse(any(state.get(f"x{index}") for index in range(11, 32)))

    def test_nested_if_else_negative_literals_and_measurement_mapping(self):
        source = program(
            """
            if (c[0] != 0) {
                if (c[1] == 1) { r1 = 7; } else { r1 = 5; }
            } else { r1 = -3; }
            r2 = r1 - c[1];
            """,
            cbits=2,
        )
        _, assembly = adapter.compile_hybrid(source)

        for measured in itertools.product((0, 1), repeat=2):
            with self.subTest(measured=measured):
                first = 7 if measured == (1, 1) else 5 if measured[0] else -3
                state = execute(assembly, measured)
                self.assertEqual(state.get("x1"), first)
                self.assertEqual(state.get("x2"), first - measured[1])

    def test_arithmetic_condition_and_optional_else(self):
        source = program(
            "r1 = 3; r2 = 4; if ((r1 + c[0]) != (r2 - 1)) { r3 = 9; }"
        )
        _, assembly = adapter.compile_hybrid(source)

        self.assertEqual(execute(assembly, (0,)).get("x3", 0), 0)
        self.assertEqual(execute(assembly, (1,)).get("x3"), 9)

    def test_random_programs_match_reference_semantics_exhaustively(self):
        rng = random.Random(20260825)
        for case in range(100):
            cbits = rng.randint(1, 3)
            first = rng.randrange(cbits)
            condition_bit = rng.randrange(cbits)
            last = rng.randrange(cbits)
            initial = rng.randint(-20, 20)
            offset = rng.randint(-20, 20)
            final = rng.randint(-20, 20)
            expected_bit = rng.randint(0, 1)
            comparator = rng.choice(("==", "!="))
            source = program(
                f"""
                r1 = {initial};
                r2 = c[{first}] + {offset};
                if (c[{condition_bit}] {comparator} {expected_bit}) {{
                    r1 = r1 + r2;
                }} else {{
                    r1 = r1 - r2;
                }}
                r3 = (r1 - c[{last}]) + {final};
                """,
                cbits=cbits,
            )
            _, assembly = adapter.compile_hybrid(source)

            for measured in itertools.product((0, 1), repeat=cbits):
                left = measured[condition_bit]
                condition = left == expected_bit
                if comparator == "!=":
                    condition = not condition
                second = measured[first] + offset
                expected_first = initial + second if condition else initial - second
                state = execute(assembly, measured)
                with self.subTest(case=case, measured=measured):
                    self.assertEqual(state.get("x1", 0), expected_first)
                    self.assertEqual(state.get("x2", 0), second)
                    self.assertEqual(
                        state.get("x3", 0), expected_first - measured[last] + final
                    )

    def test_rejects_out_of_range_measurement_bit(self):
        with self.assertRaisesRegex(ValueError, "测量位越界"):
            adapter.compile_hybrid(program("r1 = c[1];"))

    def test_random_defense_harness(self):
        report = run_defense(12345, programs=20, max_cbits=3, max_depth=3)
        self.assertEqual(report["failures"], [])


if __name__ == "__main__":
    unittest.main()
