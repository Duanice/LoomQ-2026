"""Parser and RISC-V compiler for LoomQ's Hybrid-QASM subset."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Union

try:
    from .qasm_parser import GATES
except ImportError:  # Support `python starter_kit/evaluator.py`.
    from qasm_parser import GATES


@dataclass(frozen=True)
class Literal:
    value: int


@dataclass(frozen=True)
class RegisterRef:
    index: int


@dataclass(frozen=True)
class CBitRef:
    index: int


@dataclass(frozen=True)
class Binary:
    operator: str
    left: Expression
    right: Expression


Expression = Union[Literal, RegisterRef, CBitRef, Binary]


@dataclass(frozen=True)
class Assignment:
    register: int
    expression: Expression


@dataclass(frozen=True)
class IfElse:
    comparator: str
    left: Expression
    right: Expression
    then_body: tuple[Statement, ...]
    else_body: tuple[Statement, ...]


Statement = Union[Assignment, IfElse]


@dataclass(frozen=True)
class HybridProgram:
    quantum_operations: tuple[str, ...]
    cbit_count: int
    classical_body: tuple[Statement, ...]


_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<CBIT>c\s*\[\s*\d+\s*\])|"
    r"(?P<REGISTER>r[1-9](?![A-Za-z0-9_]))|"
    r"(?P<IF>if\b)|"
    r"(?P<ELSE>else\b)|"
    r"(?P<INTEGER>\d+)|"
    r"(?P<SYMBOL>==|!=|[{}();=+\-])"
    r")"
)


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _split_classical(source: str) -> tuple[str, str]:
    match = re.search(r"\bclassical\b", source)
    if not match:
        raise ValueError("Hybrid-QASM 必须包含 classical 块")

    opening = match.end()
    while opening < len(source) and source[opening].isspace():
        opening += 1
    if opening == len(source) or source[opening] != "{":
        raise ValueError("classical 后必须是花括号块")

    depth = 0
    for closing in range(opening, len(source)):
        if source[closing] == "{":
            depth += 1
        elif source[closing] == "}":
            depth -= 1
            if depth == 0:
                quantum = source[: match.start()] + source[closing + 1 :]
                if re.search(r"\bclassical\b", quantum):
                    raise ValueError("当前文法只支持一个 classical 块")
                return quantum, source[opening + 1 : closing]
    raise ValueError("classical 块缺少右花括号")


def _quantum_operations(source: str) -> tuple[tuple[str, ...], int]:
    parts = source.split(";")
    if parts[-1].strip():
        raise ValueError("量子语句必须以分号结尾")

    header = qreg = creg = False
    cbit_count = 0
    operations = []
    for part in parts[:-1]:
        statement = re.sub(r"\s+", " ", part).strip()
        if not statement:
            continue
        if statement == "OPENQASM 2.0":
            header = True
            continue
        if statement.startswith("include "):
            continue
        if re.fullmatch(r"qreg\s+[A-Za-z_][A-Za-z0-9_]*\[\d+]", statement):
            qreg = True
            continue
        classical = re.fullmatch(
            r"creg\s+[A-Za-z_][A-Za-z0-9_]*\[(\d+)]", statement
        )
        if classical:
            creg = True
            cbit_count = int(classical.group(1))
            continue

        name = statement.split(None, 1)[0].split("(", 1)[0]
        if name != "measure" and name not in GATES:
            raise ValueError(f"不支持的量子语句: {statement!r}")
        operations.append(statement + ";")

    if not header or not qreg or not creg or cbit_count <= 0:
        raise ValueError("Hybrid-QASM 必须声明 OPENQASM、qreg 和 creg")
    return tuple(operations), cbit_count


def _tokens(source: str) -> list[tuple[str, str]]:
    tokens = []
    position = 0
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if not match:
            if source[position:].strip():
                raise ValueError(f"无法解析 classical 内容: {source[position:position + 20]!r}")
            break
        kind = match.lastgroup
        tokens.append((kind or "", match.group(kind or 0)))
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], cbit_count: int):
        self.tokens = tokens
        self.position = 0
        self.cbit_count = cbit_count

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _accept(self, value: str) -> bool:
        token = self._peek()
        if token and token[1] == value:
            self.position += 1
            return True
        return False

    def _expect(self, value: str) -> None:
        if not self._accept(value):
            raise ValueError(f"期望 {value!r}，实际为 {self._peek()!r}")

    def parse(self) -> tuple[Statement, ...]:
        statements = self._statements(until=None)
        if self._peek() is not None:
            raise ValueError(f"多余的 classical token: {self._peek()!r}")
        return statements

    def _statements(self, until: str | None) -> tuple[Statement, ...]:
        statements = []
        while self._peek() is not None and (until is None or self._peek()[1] != until):
            statements.append(self._statement())
        return tuple(statements)

    def _statement(self) -> Statement:
        token = self._peek()
        if token and token[0] == "IF":
            return self._if_else()
        if token and token[0] == "REGISTER":
            self.position += 1
            register = int(token[1][1:])
            self._expect("=")
            expression = self._arithmetic()
            self._expect(";")
            return Assignment(register, expression)
        raise ValueError(f"不支持的 classical 语句: {token!r}")

    def _if_else(self) -> IfElse:
        self.position += 1
        self._expect("(")
        left = self._arithmetic()
        comparator = self._peek()
        if not comparator or comparator[1] not in ("==", "!="):
            raise ValueError("if 条件必须使用 == 或 !=")
        self.position += 1
        right = self._arithmetic()
        self._expect(")")
        then_body = self._block()
        else_body: tuple[Statement, ...] = ()
        if self._peek() and self._peek()[0] == "ELSE":
            self.position += 1
            else_body = self._block()
        return IfElse(comparator[1], left, right, then_body, else_body)

    def _block(self) -> tuple[Statement, ...]:
        self._expect("{")
        statements = self._statements(until="}")
        self._expect("}")
        return statements

    def _arithmetic(self) -> Expression:
        expression = self._primary()
        while self._peek() and self._peek()[1] in ("+", "-"):
            operator = self._peek()[1]
            self.position += 1
            expression = Binary(operator, expression, self._primary())
        return expression

    def _primary(self) -> Expression:
        token = self._peek()
        if not token:
            raise ValueError("表达式意外结束")
        if token[1] == "-":
            self.position += 1
            return Binary("-", Literal(0), self._primary())
        if token[1] == "(":
            self.position += 1
            expression = self._arithmetic()
            self._expect(")")
            return expression
        self.position += 1
        if token[0] == "INTEGER":
            return Literal(int(token[1]))
        if token[0] == "REGISTER":
            return RegisterRef(int(token[1][1:]))
        if token[0] == "CBIT":
            index = int(re.search(r"\d+", token[1]).group())
            if index >= self.cbit_count or index > 21:
                raise ValueError(f"测量位越界: c[{index}]")
            return CBitRef(index)
        raise ValueError(f"不支持的表达式 token: {token!r}")


def parse_hybrid(source: str) -> HybridProgram:
    source = _strip_comments(source)
    quantum_source, classical_source = _split_classical(source)
    quantum_operations, cbit_count = _quantum_operations(quantum_source)
    body = _Parser(_tokens(classical_source), cbit_count).parse()
    return HybridProgram(quantum_operations, cbit_count, body)


class _Compiler:
    def __init__(self, cbit_count: int):
        self.lines: list[str] = []
        self.available = list(range(10 + cbit_count, 32))
        self.label_counter = 0

    def compile(self, statements: tuple[Statement, ...]) -> str:
        self._statements(statements)
        if not self.lines:
            self.lines.append("addi x0, x0, 0")
        return "\n".join(self.lines) + "\n"

    def _acquire(self) -> int:
        if not self.available:
            raise ValueError("表达式过深，没有可用的临时寄存器")
        return self.available.pop()

    def _release(self, register: int) -> None:
        self.lines.append(f"li x{register}, 0")
        self.available.append(register)

    def _label(self, prefix: str) -> str:
        label = f"L_{prefix}_{self.label_counter}"
        self.label_counter += 1
        return label

    def _statements(self, statements: tuple[Statement, ...]) -> None:
        for statement in statements:
            if isinstance(statement, Assignment):
                value = self._expression(statement.expression)
                self.lines.append(f"addi x{statement.register}, x{value}, 0")
                self._release(value)
            else:
                self._if_else(statement)

    def _expression(self, expression: Expression) -> int:
        if isinstance(expression, Literal):
            result = self._acquire()
            self.lines.append(f"li x{result}, {expression.value}")
            return result
        if isinstance(expression, RegisterRef):
            result = self._acquire()
            self.lines.append(f"addi x{result}, x{expression.index}, 0")
            return result
        if isinstance(expression, CBitRef):
            result = self._acquire()
            self.lines.append(f"addi x{result}, x{10 + expression.index}, 0")
            return result

        left = self._expression(expression.left)
        right = self._expression(expression.right)
        instruction = "add" if expression.operator == "+" else "sub"
        self.lines.append(f"{instruction} x{left}, x{left}, x{right}")
        self._release(right)
        return left

    def _if_else(self, statement: IfElse) -> None:
        left = self._expression(statement.left)
        right = self._expression(statement.right)
        else_label = self._label("else")
        end_label = self._label("end")
        branch = "bne" if statement.comparator == "==" else "beq"
        self.lines.append(f"{branch} x{left}, x{right}, {else_label}")

        self._clear_condition(left, right)
        self._statements(statement.then_body)
        self.lines.append(f"j {end_label}")
        self.lines.append(f"{else_label}:")
        self.lines.extend((f"li x{left}, 0", f"li x{right}, 0"))
        self._statements(statement.else_body)
        self.lines.append(f"{end_label}:")

    def _clear_condition(self, left: int, right: int) -> None:
        self.lines.extend((f"li x{left}, 0", f"li x{right}, 0"))
        self.available.extend((left, right))


def compile_hybrid(source: str) -> tuple[list[str], str]:
    program = parse_hybrid(source)
    assembly = _Compiler(program.cbit_count).compile(program.classical_body)
    return list(program.quantum_operations), assembly
