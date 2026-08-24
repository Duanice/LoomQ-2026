# LoomQ Quantum RISC-V Extension v1.0

本扩展在官方 `riscv_emulator.py` 的轻量文本模拟器上实现一个最小但真实的
32 位指令闭环：量子操作先编码为机器字，模拟器在执行阶段检查 custom opcode、
解码字段并改变量子态；测量结果写回通用寄存器，后续经典 RISC-V 指令可直接使用。
遵循官方 Bonus 口径，扩展只为新增的量子指令实现机器码路径；基础
`li/add/sub/addi/beq/bne/j` 仍使用官方文本解释路径，不重复实现完整汇编器、链接器
或通用 RV32I 二进制解码器。

```text
共享 Circuit IR
      │ encode_quantum_circuit()
      ▼
32 位机器字（opcode = custom-0）
      │ TinyRISCVEmulator.load_machine_program()
      ▼
逐字校验与解码 ──► 量子门 / 测量坍缩 ──► x10..x31 ──► 经典控制流
```

这不是只存在于文档中的 opcode：测试同时覆盖固定机器字、编码/解码、量子态执行、
测量写回以及量子与经典指令混合运行。

## 1. 公共字段

所有量子指令均为 32 位 RISC-V instruction word，使用 RISC-V 为非标准扩展保留的
`custom-0` major opcode；若序列化为字节，则采用 RISC-V 小端顺序：

```text
bits [6:0] = 0001011 = 0x0B
```

量子比特字段宽 5 位，可编码 `q0..q31`。参考状态向量模拟器为防止意外耗尽内存，
将单次执行限制为 16 个量子比特；该限制不改变指令编码。

## 2. 无参数门与测量：R-type

```text
 31          25 24       20 19       15 14    12 11        7 6          0
+---------------+-----------+-----------+--------+-----------+------------+
|    funct7     |    qs2    |    qs1    |  000   | qd / xrd  |  0001011   |
+---------------+-----------+-----------+--------+-----------+------------+
```

| 助记符 | funct7 | `qs1` | `qs2` | `qd / xrd` | 语义 |
|---|---:|---|---|---|---|
| `qh qd` | `0x01` | 0 | 0 | 目标 | Hadamard |
| `qx qd` | `0x02` | 0 | 0 | 目标 | Pauli-X |
| `qs qd` | `0x03` | 0 | 0 | 目标 | S |
| `qsdg qd` | `0x04` | 0 | 0 | 目标 | S† |
| `qt qd` | `0x05` | 0 | 0 | 目标 | T |
| `qtdg qd` | `0x06` | 0 | 0 | 目标 | T† |
| `qcx qs1, qd` | `0x07` | 控制 | 0 | 目标 | controlled-X |
| `qswap qs1, qd` | `0x08` | 第一个 | 0 | 第二个 | SWAP |
| `qccx qs1, qs2, qd` | `0x09` | 控制 1 | 控制 2 | 目标 | Toffoli |
| `qmeasure qs1, xrd` | `0x0A` | 被测量位 | 0 | 经典目标 | 测量并写回 0/1 |

未使用字段必须为零；`qmeasure` 禁止写入恒为零的 `x0`。编码器也拒绝同一个门重复
使用同一量子比特。

## 3. 参数门：I-type

```text
 31                      20 19       15 14    12 11        7 6          0
+--------------------------+-----------+--------+-----------+------------+
|    angle_imm[11:0]       |    qs1    | funct3 |    qd     |  0001011   |
+--------------------------+-----------+--------+-----------+------------+
```

| 助记符 | funct3 | `qs1` | `qd` |
|---|---:|---|---|
| `qry qd, angle` | `001` | 0 | 目标 |
| `qrz qd, angle` | `010` | 0 | 目标 |
| `qcu1 qs1, qd, angle` | `011` | 控制 | 目标 |

角度先按 `2π` 归一化，再编码为有符号 12 位二补数定点数：

```text
ANGLE_SCALE = π / 1024
angle_imm = round(normalize(angle) / ANGLE_SCALE)
decoded_angle = sign_extend(angle_imm) × ANGLE_SCALE
```

最大量化误差为 `π/2048`。汇编入口支持安全的数字与 `pi` 算术表达式，不使用
Python `eval()`。

## 4. 执行语义

1. 量子寄存器初始化为 `|00...0⟩`。
2. 12 个白名单门复用 LoomQ 共享状态向量语义执行。
3. `qmeasure` 按 Born 概率采样、坍缩并归一化量子态，然后把结果写入 `xrd`。
4. 程序继续执行，`add/sub/beq` 等官方经典指令可以读取测量寄存器。
5. 非 `0x0B` opcode、保留 `funct3/funct7`、非零保留字段和越界操作数均立即拒绝。

从共享 IR 编码测量时，`c[k]` 映射到 `x10+k`，与 L3 基础任务保持一致。

## 5. Bell 机器字示例

```text
qh       q0       -> 0x0200000b
qcx      q0, q1   -> 0x0e00008b
qmeasure q0, x10  -> 0x1400050b
qmeasure q1, x11  -> 0x1400858b
```

模拟器既接受这些 q-prefixed 助记符，也接受 `.word 0x0200000b` 形式的原始机器字。
两条测量之后，`x10 == x11`；重复执行只会观察到 `00` 和 `11`。

## 6. 实现与复现

- 编码、解码和执行：`starter_kit/riscv_emulator.py`
- 可归档端到端测试：`starter_kit/test_quantum_riscv.py`
- 无第三方依赖，使用 Python 3.10 标准库和仓库已有共享状态向量实现。

在 fork 根目录运行：

```bash
python3 -m unittest starter_kit.test_quantum_riscv -v
```

测试覆盖：

- 全部 12 个 L1 白名单门和测量的机器字往返；
- 已知固定编码 `qh q3 == 0x0200018b`；
- Bell-2 与 GHZ-3 的端到端分布；
- 原始机器字与经典 `add` 指令混合执行；
- 连续测量证明坍缩；
- 错误 opcode、保留字段、重复量子比特和非有限角度的拒绝路径。
