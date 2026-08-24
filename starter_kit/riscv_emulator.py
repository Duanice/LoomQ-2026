#!/usr/bin/env python3
"""Tiny RISC-V emulator plus LoomQ's executable quantum custom extension."""

from dataclasses import dataclass
import math
import random
from typing import Dict, List, Tuple

try:
    from .qasm_parser import Circuit, Operation
    from .simulator import apply_operation, evaluate_angle
except ImportError:  # Support ``python starter_kit/riscv_emulator.py``.
    from qasm_parser import Circuit, Operation
    from simulator import apply_operation, evaluate_angle


QUANTUM_OPCODE = 0x0B  # RISC-V custom-0 major opcode.
ANGLE_SCALE = math.pi / 1024
MAX_ABS_ANGLE = 1_000_000_000_000.0
MAX_EMULATED_QUBITS = 16

_R_GATE_CODES = {
    "h": 0x01,
    "x": 0x02,
    "s": 0x03,
    "sdg": 0x04,
    "t": 0x05,
    "tdg": 0x06,
    "cx": 0x07,
    "swap": 0x08,
    "ccx": 0x09,
    "measure": 0x0A,
}
_R_CODE_GATES = {code: name for name, code in _R_GATE_CODES.items()}
_I_GATE_FUNCT3 = {"ry": 0x1, "rz": 0x2, "cu1": 0x3}
_I_FUNCT3_GATES = {code: name for name, code in _I_GATE_FUNCT3.items()}
_GATE_ARITY = {
    "h": 1,
    "x": 1,
    "s": 1,
    "sdg": 1,
    "t": 1,
    "tdg": 1,
    "ry": 1,
    "rz": 1,
    "cx": 2,
    "cu1": 2,
    "swap": 2,
    "ccx": 3,
}


@dataclass(frozen=True)
class QuantumInstruction:
    """Decoded semantic form of one 32-bit LoomQ quantum instruction."""

    name: str
    qubits: tuple[int, ...]
    parameter: float | None = None
    destination: int | None = None


def _field(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 32:
        raise ValueError(f"{name} 必须在 0..31 范围内")
    return value


def _validate_qubits(name: str, qubits: tuple[int, ...]) -> None:
    expected = _GATE_ARITY[name]
    if len(qubits) != expected:
        raise ValueError(f"{name} 需要 {expected} 个量子比特")
    for qubit in qubits:
        _field(qubit, "量子比特")
    if len(set(qubits)) != len(qubits):
        raise ValueError(f"{name} 的量子比特不能重复")


def encode_quantum_instruction(
    name: str,
    qubits: tuple[int, ...],
    parameter: float | None = None,
    destination: int | None = None,
) -> int:
    """Encode one quantum operation into a real 32-bit custom-0 word."""

    name = name.lower()
    if name == "measure":
        if len(qubits) != 1:
            raise ValueError("measure 需要 1 个量子比特")
        qubit = _field(qubits[0], "量子比特")
        target = _field(destination if destination is not None else -1, "目标寄存器")
        if target == 0:
            raise ValueError("measure 不能写入恒为零的 x0")
        return (
            (_R_GATE_CODES[name] << 25)
            | (qubit << 15)
            | (target << 7)
            | QUANTUM_OPCODE
        )

    if name not in _GATE_ARITY:
        raise ValueError(f"不支持的量子扩展指令: {name}")
    _validate_qubits(name, qubits)

    if name in _I_GATE_FUNCT3:
        if parameter is None or not math.isfinite(parameter):
            raise ValueError(f"{name} 需要有限角度参数")
        if abs(parameter) > MAX_ABS_ANGLE:
            raise ValueError(
                f"{name} 角度绝对值不能超过 {MAX_ABS_ANGLE:g} 弧度"
            )
        normalized = (parameter + math.pi) % (2 * math.pi) - math.pi
        immediate = round(normalized / ANGLE_SCALE)
        target = qubits[-1]
        control = qubits[0] if name == "cu1" else 0
        return (
            ((immediate & 0xFFF) << 20)
            | (control << 15)
            | (_I_GATE_FUNCT3[name] << 12)
            | (target << 7)
            | QUANTUM_OPCODE
        )

    target = qubits[-1]
    first = qubits[0] if len(qubits) >= 2 else 0
    second = qubits[1] if len(qubits) == 3 else 0
    return (
        (_R_GATE_CODES[name] << 25)
        | (second << 20)
        | (first << 15)
        | (target << 7)
        | QUANTUM_OPCODE
    )


def decode_quantum_instruction(word: int) -> QuantumInstruction:
    """Decode and validate one 32-bit LoomQ quantum instruction word."""

    if not isinstance(word, int) or isinstance(word, bool) or not 0 <= word < 2**32:
        raise ValueError("机器指令必须是一个 32 位无符号整数")
    if word & 0x7F != QUANTUM_OPCODE:
        raise ValueError(f"不是 LoomQ custom-0 指令: 0x{word:08x}")

    target = (word >> 7) & 0x1F
    funct3 = (word >> 12) & 0x7
    first = (word >> 15) & 0x1F
    second = (word >> 20) & 0x1F
    funct7 = (word >> 25) & 0x7F

    if funct3:
        name = _I_FUNCT3_GATES.get(funct3)
        if name is None:
            raise ValueError(f"保留的量子 funct3: {funct3}")
        immediate = (word >> 20) & 0xFFF
        if immediate & 0x800:
            immediate -= 0x1000
        qubits = (first, target) if name == "cu1" else (target,)
        _validate_qubits(name, qubits)
        if name != "cu1" and first != 0:
            raise ValueError(f"{name} 的 rs1 保留字段必须为 0")
        return QuantumInstruction(name, qubits, immediate * ANGLE_SCALE)

    name = _R_CODE_GATES.get(funct7)
    if name is None:
        raise ValueError(f"保留的量子 funct7: {funct7}")
    if name == "measure":
        if second != 0:
            raise ValueError("measure 的 rs2 保留字段必须为 0")
        if target == 0:
            raise ValueError("measure 不能写入恒为零的 x0")
        return QuantumInstruction(name, (first,), destination=target)

    arity = _GATE_ARITY[name]
    qubits = (
        (target,)
        if arity == 1
        else (first, target)
        if arity == 2
        else (first, second, target)
    )
    _validate_qubits(name, qubits)
    if arity == 1 and (first != 0 or second != 0):
        raise ValueError(f"{name} 的 rs1/rs2 保留字段必须为 0")
    if arity == 2 and second != 0:
        raise ValueError(f"{name} 的 rs2 保留字段必须为 0")
    return QuantumInstruction(name, qubits)


def encode_quantum_circuit(circuit: Circuit) -> list[int]:
    """Translate the shared Circuit IR into executable quantum machine words."""

    words = [
        encode_quantum_instruction(
            operation.name,
            operation.qubits,
            evaluate_angle(operation.parameter) if operation.parameter else None,
        )
        for operation in circuit.operations
    ]
    words.extend(
        encode_quantum_instruction(
            "measure", (measurement.qubit,), destination=10 + measurement.cbit
        )
        for measurement in circuit.measurements
    )
    return words


def _qubit(token: str) -> int:
    if not token.lower().startswith("q") or not token[1:].isdigit():
        raise ValueError(f"无效的量子比特名称: {token}")
    return _field(int(token[1:]), "量子比特")


def assemble_quantum_instruction(op: str, args: list[str]) -> int:
    """Assemble one q-prefixed mnemonic into its 32-bit instruction word."""

    name = op.lower().removeprefix("q")
    if name == "measure":
        if len(args) != 2:
            raise ValueError("qmeasure 语法: qmeasure qN, xN")
        destination = TinyRISCVEmulator._parse_reg_idx_value(args[1])
        return encode_quantum_instruction(
            name, (_qubit(args[0]),), destination=destination
        )
    if name in _I_GATE_FUNCT3:
        arity = _GATE_ARITY[name]
        if len(args) < arity + 1:
            raise ValueError(f"q{name} 需要 {arity} 个量子比特和 1 个角度")
        return encode_quantum_instruction(
            name,
            tuple(_qubit(token) for token in args[:arity]),
            evaluate_angle("".join(args[arity:])),
        )
    if name not in _GATE_ARITY or len(args) != _GATE_ARITY.get(name, -1):
        raise ValueError(f"无效的量子助记符或参数数量: {op}")
    return encode_quantum_instruction(name, tuple(_qubit(token) for token in args))


class TinyRISCVEmulator:
    def __init__(self, seed: int = 0):
        # 32个通用寄存器 x0 - x31，x0 恒为 0
        self.registers = [0] * 32
        self.pc = 0
        self.labels: Dict[str, int] = {}
        self.instructions: List[Tuple[str, List[str]]] = []
        self.max_steps = 1000  # 防止死循环
        self.random = random.Random(seed)
        self.qubit_count = 0
        self.quantum_state = [1 + 0j]

    def set_register(self, reg: str, value: int):
        idx = self._parse_reg_idx(reg)
        if idx != 0:
            self.registers[idx] = value

    def get_register(self, reg: str) -> int:
        idx = self._parse_reg_idx(reg)
        return self.registers[idx]

    def _parse_reg_idx(self, reg: str) -> int:
        return self._parse_reg_idx_value(reg)

    @staticmethod
    def _parse_reg_idx_value(reg: str) -> int:
        reg = reg.strip().replace(",", "")
        if not reg.startswith("x") and not reg.startswith("X"):
            raise ValueError(f"无效的寄存器名称: {reg}")
        idx = int(reg[1:])
        if idx < 0 or idx > 31:
            raise ValueError(f"寄存器索引超出范围 (x0-x31): {reg}")
        return idx

    def load_program(self, asm_code: str, qubit_count: int | None = None):
        """
        Parse classical assembly plus q* mnemonics or raw ``.word`` values.

        Quantum mnemonics are assembled immediately, stored as 32-bit words,
        and decoded only when execution reaches them.
        """
        self.instructions = []
        self.labels = {}
        self.pc = 0
        self.registers = [0] * 32
        
        lines = asm_code.split("\n")
        temp_instructions = []
        
        # 第一次解析：过滤注释、空行并建立指令列表与 Label 映射
        for line in lines:
            line = line.strip()
            # 过滤注释和空行
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            
            # 分割行内注释
            if "#" in line:
                line = line.split("#")[0].strip()
            
            # 提取标签，例如 "LABEL_A:"
            if line.endswith(":"):
                label_name = line[:-1].strip()
                self.labels[label_name] = len(temp_instructions)
                continue
            elif ":" in line:
                # 处理同行的标签，例如 "LOOP: li x1, 10"
                parts = line.split(":", 1)
                label_name = parts[0].strip()
                self.labels[label_name] = len(temp_instructions)
                line = parts[1].strip()
            
            # 解析指令和参数
            tokens = line.replace(",", " ").split()
            op = tokens[0].lower()
            args = tokens[1:]
            if op.startswith("q"):
                word = assemble_quantum_instruction(op, args)
                op, args = ".word", [f"0x{word:08x}"]
            elif op == ".word":
                if len(args) != 1:
                    raise ValueError(".word 必须且只能包含一个 32 位指令")
                word = int(args[0], 0)
                decode_quantum_instruction(word)
                args = [f"0x{word:08x}"]
            temp_instructions.append((op, args))
            
        self.instructions = temp_instructions

        decoded = [
            decode_quantum_instruction(int(args[0], 0))
            for op, args in self.instructions
            if op == ".word"
        ]
        required = 1 + max(
            (qubit for instruction in decoded for qubit in instruction.qubits),
            default=-1,
        )
        if qubit_count is None:
            qubit_count = required
        if not isinstance(qubit_count, int) or isinstance(qubit_count, bool):
            raise ValueError("qubit_count 必须是整数")
        if qubit_count < required:
            raise ValueError(f"程序需要至少 {required} 个量子比特")
        if not 0 <= qubit_count <= MAX_EMULATED_QUBITS:
            raise ValueError(
                f"参考模拟器支持 0..{MAX_EMULATED_QUBITS} 个量子比特"
            )
        self.qubit_count = qubit_count
        self.quantum_state = [0j] * (1 << qubit_count)
        self.quantum_state[0] = 1 + 0j

    def load_machine_program(
        self, words: list[int], qubit_count: int | None = None
    ) -> None:
        """Load raw instruction words without bypassing the decoder."""

        self.load_program(
            "\n".join(f".word 0x{word:08x}" for word in words), qubit_count
        )

    def _measure(self, instruction: QuantumInstruction) -> None:
        qubit = instruction.qubits[0]
        probability_one = sum(
            abs(amplitude) ** 2
            for index, amplitude in enumerate(self.quantum_state)
            if (index >> qubit) & 1
        )
        outcome = (
            0
            if probability_one <= 0
            else 1
            if probability_one >= 1
            else int(self.random.random() < probability_one)
        )
        probability = probability_one if outcome else 1 - probability_one
        norm = math.sqrt(probability)
        for index, amplitude in enumerate(self.quantum_state):
            self.quantum_state[index] = (
                amplitude / norm if ((index >> qubit) & 1) == outcome else 0j
            )
        self.set_register(f"x{instruction.destination}", outcome)

    def _execute_quantum_word(self, text: str) -> None:
        instruction = decode_quantum_instruction(int(text, 0))
        if instruction.name == "measure":
            self._measure(instruction)
            return
        apply_operation(
            self.quantum_state,
            Operation(
                instruction.name,
                instruction.qubits,
                str(instruction.parameter)
                if instruction.parameter is not None
                else None,
            ),
        )

    def execute(self) -> Dict[str, int]:
        """
        执行已载入的指令直到程序结束，返回所有寄存器状态字典
        """
        steps = 0
        num_instr = len(self.instructions)
        
        while 0 <= self.pc < num_instr:
            steps += 1
            if steps > self.max_steps:
                raise RuntimeError("程序执行超出最大步数限制，疑似发生死循环")
                
            op, args = self.instructions[self.pc]
            next_pc = self.pc + 1
            
            # 模拟执行各指令
            if op == "li":
                # li rd, imm
                rd, imm = args[0], int(args[1])
                self.set_register(rd, imm)
                
            elif op == "add":
                # add rd, rs1, rs2
                rd, rs1, rs2 = args[0], args[1], args[2]
                self.set_register(rd, self.get_register(rs1) + self.get_register(rs2))
                
            elif op == "sub":
                # sub rd, rs1, rs2
                rd, rs1, rs2 = args[0], args[1], args[2]
                self.set_register(rd, self.get_register(rs1) - self.get_register(rs2))
                
            elif op == "addi":
                # addi rd, rs1, imm
                rd, rs1, imm = args[0], args[1], int(args[2])
                self.set_register(rd, self.get_register(rs1) + imm)
                
            elif op == "beq":
                # beq rs1, rs2, label
                rs1, rs2, label = args[0], args[1], args[2]
                if self.get_register(rs1) == self.get_register(rs2):
                    if label not in self.labels:
                        raise ValueError(f"未定义的跳转标签: {label}")
                    next_pc = self.labels[label]
                    
            elif op == "bne":
                # bne rs1, rs2, label
                rs1, rs2, label = args[0], args[1], args[2]
                if self.get_register(rs1) != self.get_register(rs2):
                    if label not in self.labels:
                        raise ValueError(f"未定义的跳转标签: {label}")
                    next_pc = self.labels[label]
                    
            elif op == "j":
                # j label
                label = args[0]
                if label not in self.labels:
                    raise ValueError(f"未定义的跳转标签: {label}")
                next_pc = self.labels[label]

            elif op == ".word":
                self._execute_quantum_word(args[0])
                
            else:
                raise ValueError(f"不支持的指令操作: {op}")
                
            self.pc = next_pc
            
        # 返回非零寄存器的状态汇总
        result = {}
        for idx, val in enumerate(self.registers):
            if val != 0:
                result[f"x{idx}"] = val
        return result


def run_quantum_circuit(circuit: Circuit, shots: int, seed: int = 0) -> dict[str, int]:
    """Execute shared Circuit IR through encode -> word -> decode -> emulator."""

    if not isinstance(shots, int) or isinstance(shots, bool) or shots <= 0:
        raise ValueError("shots 必须是正整数")
    words = encode_quantum_circuit(circuit)
    seeds = random.Random(seed)
    counts: dict[str, int] = {}
    for _ in range(shots):
        emulator = TinyRISCVEmulator(seeds.randrange(2**63))
        emulator.load_machine_program(words, circuit.qubit_count)
        registers = emulator.execute()
        key = "".join(
            str(registers.get(f"x{10 + cbit}", 0))
            for cbit in reversed(range(circuit.cbit_count))
        )
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))

# 简易功能测试
if __name__ == "__main__":
    code = """
    li x1, 5
    li x2, 10
    beq x1, x2, EQUAL
    add x3, x1, x2       # x3 = 15
    j END
    EQUAL:
    sub x3, x2, x1
    END:
    addi x3, x3, 1       # x3 = 16
    """
    emu = TinyRISCVEmulator()
    emu.load_program(code)
    state = emu.execute()
    print("寄存器执行最终状态:", state)
    assert state.get("x3") == 16, "测试失败！"
    print("Tiny RISC-V 模拟器核心测试通过！")
