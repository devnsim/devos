# ==============================================================================
# FILE: app/modules/spreadsheet/formulas.py
# OVERVIEW: A small, safe spreadsheet formula language (no eval): numbers, text, cell references (A1,
#           $B$2), ranges (A1:C9), + - * / ^ % & and comparisons, and common functions (SUM, AVERAGE,
#           IF, ROUND ...). Used by the Spreadsheet module and the Calculator tool.
# PURPOSE:
#   1. tokenize() -> parse() -> an expression tree; evaluate(tree, resolver) computes it.
#   2. evaluate_expression(text): a formula without cell references (the Calculator).
#   3. Spreadsheet error values (#DIV/0!, #REF!, #NAME?, #VALUE!, #CYCLE!, #ERROR!) as FormulaError.
# CONTRACT:
#   - Requires: nothing outside the standard library. No Qt.
#   - Exposes: FormulaError, tokenize(text), parse(text), evaluate(tree, resolver), evaluate_expression(text),
#              FUNCTIONS, column_name(index), column_index(letters), parse_cell(ref) -> (row, col)
#   - resolver: object with cell(row, col) -> value and cells(row1, col1, row2, col2) -> list of values.
# NOTES:
#   - [2026-11-16] Created with the Spreadsheet module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - ref: A cell address like B12 (0-based (row, col) = (11, 1) inside DevOS); $ marks are accepted.
# - error value: A string like "#DIV/0!" shown in a cell whose formula failed (FormulaError.code).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. LANGUAGE
#    1.1 references
#    1.2 tokenize
#    1.3 parse
# 2. EVALUATION
#    2.1 functions
#    2.2 evaluate
# ==============================================================================

# ==============================================================================
# [1.1] references
# ==============================================================================
from __future__ import annotations

import math
import re
import statistics


class FormulaError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def parse_cell(ref: str) -> tuple[int, int]:
    match = _CELL.match(ref)
    if not match or int(match.group(2)) < 1:
        raise FormulaError("#REF!", f"bad reference {ref}")
    return int(match.group(2)) - 1, column_index(match.group(1))
# --- END [1.1] ---

# ==============================================================================
# [1.2] tokenize
# ==============================================================================
_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<number>\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<range>\$?[A-Za-z]{1,3}\$?\d+:\$?[A-Za-z]{1,3}\$?\d+)
  | (?P<ref>\$[A-Za-z]{1,3}\$?\d+|[A-Za-z]{1,3}\$\d+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*\$?\d*)
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),:])
""", re.VERBOSE)


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise FormulaError("#ERROR!", f"unexpected “{text[position]}”")
        kind = match.lastgroup
        value = match.group(kind)
        position = match.end()
        if kind == "space":
            continue
        if kind == "name" and _CELL.match(value):
            kind = "ref"
        tokens.append((kind, value))
    return tokens
# --- END [1.2] ---

# ==============================================================================
# [1.3] parse
# ==============================================================================
class _Parser:
    COMPARISONS = ("=", "<>", "<", ">", "<=", ">=")

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens, self.position = tokens, 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (value is not None and token[1] != value):
            raise FormulaError("#ERROR!", f"expected “{value}”" if value else "formula ends too early")
        self.position += 1
        return token

    def is_op(self, *values: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "op" and token[1] in values

    def parse(self):
        tree = self.comparison()
        if self.peek() is not None:
            raise FormulaError("#ERROR!", f"unexpected “{self.peek()[1]}”")
        return tree

    def comparison(self):
        tree = self.concat()
        while self.is_op(*self.COMPARISONS):
            tree = ("binary", self.take()[1], tree, self.concat())
        return tree

    def concat(self):
        tree = self.additive()
        while self.is_op("&"):
            tree = ("binary", self.take()[1], tree, self.additive())
        return tree

    def additive(self):
        tree = self.term()
        while self.is_op("+", "-"):
            tree = ("binary", self.take()[1], tree, self.term())
        return tree

    def term(self):
        tree = self.power()
        while self.is_op("*", "/"):
            tree = ("binary", self.take()[1], tree, self.power())
        return tree

    def power(self):
        tree = self.unary()
        while self.is_op("^"):
            tree = ("binary", self.take()[1], tree, self.unary())
        return tree

    def unary(self):
        if self.is_op("-", "+"):
            operator = self.take()[1]
            operand = self.unary()
            return ("negate", operand) if operator == "-" else operand
        tree = self.primary()
        while self.is_op("%"):
            self.take()
            tree = ("binary", "/", tree, ("number", 100.0))
        return tree

    def primary(self):
        token = self.take()
        kind, value = token
        if kind == "number":
            return ("number", float(value))
        if kind == "string":
            return ("string", value[1:-1].replace('""', '"'))
        if kind == "ref":
            return ("ref", *parse_cell(value))
        if kind == "range":
            first, second = value.split(":")
            (row1, col1), (row2, col2) = parse_cell(first), parse_cell(second)
            return ("range", min(row1, row2), min(col1, col2), max(row1, row2), max(col1, col2))
        if kind == "name":
            name = value.upper()
            if name in ("TRUE", "FALSE") and not self.is_op("("):
                return ("bool", name == "TRUE")
            if not self.is_op("("):
                raise FormulaError("#NAME?", f"unknown name {value}")
            self.take("(")
            arguments = []
            if not self.is_op(")"):
                arguments.append(self.comparison())
                while self.is_op(","):
                    self.take()
                    arguments.append(self.comparison())
            self.take(")")
            return ("call", name, arguments)
        if kind == "op" and value == "(":
            tree = self.comparison()
            self.take(")")
            return tree
        raise FormulaError("#ERROR!", f"unexpected “{value}”")


def parse(text: str):
    """Formula text (with or without the leading =) -> expression tree."""
    text = text[1:] if text.startswith("=") else text
    if not text.strip():
        raise FormulaError("#ERROR!", "empty formula")
    return _Parser(tokenize(text)).parse()
# --- END [1.3] ---


# ==============================================================================
# [2.1] functions
# ==============================================================================
def _numbers(values: list) -> list[float]:
    """Numbers among the values (text and blanks are skipped, like spreadsheets do for ranges)."""
    flat = []
    for value in values:
        if isinstance(value, list):
            flat += _numbers(value)
        elif isinstance(value, bool):
            flat.append(float(value))
        elif isinstance(value, (int, float)):
            flat.append(float(value))
    return flat


def _flat(values: list) -> list:
    out = []
    for value in values:
        out += _flat(value) if isinstance(value, list) else [value]
    return out


def _number(value) -> float:
    if isinstance(value, list):
        raise FormulaError("#VALUE!", "a range where a single value was expected")
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise FormulaError("#VALUE!", f"“{value}” is not a number") from None


def _average(values: list) -> float:
    numbers = _numbers(values)
    if not numbers:
        raise FormulaError("#DIV/0!", "no numbers to average")
    return sum(numbers) / len(numbers)


def _sqrt(value: float) -> float:
    if value < 0:
        raise FormulaError("#VALUE!", "square root of a negative number")
    return math.sqrt(value)


def _divide_check(value: float) -> float:
    if value == 0:
        raise FormulaError("#DIV/0!", "division by zero")
    return value


FUNCTIONS = {
    "SUM": lambda args: sum(_numbers(args)),
    "AVERAGE": _average,
    "MIN": lambda args: min(_numbers(args), default=0.0),
    "MAX": lambda args: max(_numbers(args), default=0.0),
    "MEDIAN": lambda args: statistics.median(_numbers(args)) if _numbers(args) else 0.0,
    "PRODUCT": lambda args: math.prod(_numbers(args)) if _numbers(args) else 0.0,
    "COUNT": lambda args: float(len(_numbers(args))),
    "COUNTA": lambda args: float(sum(1 for value in _flat(args) if value not in (None, ""))),
    "ROUND": lambda args: round(_number(args[0]), int(_number(args[1])) if len(args) > 1 else 0),
    "INT": lambda args: float(math.floor(_number(args[0]))),
    "ABS": lambda args: abs(_number(args[0])),
    "SQRT": lambda args: _sqrt(_number(args[0])),
    "POWER": lambda args: _number(args[0]) ** _number(args[1]),
    "MOD": lambda args: _number(args[0]) % _divide_check(_number(args[1])),
    "PI": lambda args: math.pi,
    "AND": lambda args: all(bool(value) for value in _flat(args)),
    "OR": lambda args: any(bool(value) for value in _flat(args)),
    "NOT": lambda args: not bool(args[0]),
    "LEN": lambda args: float(len(_text(args[0]))),
    "UPPER": lambda args: _text(args[0]).upper(),
    "LOWER": lambda args: _text(args[0]).lower(),
    "CONCAT": lambda args: "".join(_text(value) for value in _flat(args)),
}
LAZY_FUNCTIONS = ("IF", "IFERROR")             # evaluate only the branch they need


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
# --- END [2.1] ---

# ==============================================================================
# [2.2] evaluate
# ==============================================================================
def evaluate(tree, resolver):
    kind = tree[0]
    if kind in ("number", "string", "bool"):
        return tree[1]
    if kind == "ref":
        return resolver.cell(tree[1], tree[2])
    if kind == "range":
        return resolver.cells(*tree[1:])
    if kind == "negate":
        return -_number(evaluate(tree[1], resolver))
    if kind == "call":
        name, arguments = tree[1], tree[2]
        if name == "IF":
            if len(arguments) < 2:
                raise FormulaError("#VALUE!", "IF needs a condition and a value")
            if evaluate(arguments[0], resolver):
                return evaluate(arguments[1], resolver)
            return evaluate(arguments[2], resolver) if len(arguments) > 2 else False
        if name == "IFERROR":
            try:
                return evaluate(arguments[0], resolver)
            except FormulaError:
                return evaluate(arguments[1], resolver) if len(arguments) > 1 else ""
        function = FUNCTIONS.get(name)
        if function is None:
            raise FormulaError("#NAME?", f"unknown function {name}")
        try:
            return function([evaluate(argument, resolver) for argument in arguments])
        except IndexError:
            raise FormulaError("#VALUE!", f"{name} needs more arguments") from None
    if kind == "binary":
        operator = tree[1]
        left, right = evaluate(tree[2], resolver), evaluate(tree[3], resolver)
        if operator == "&":
            return _text(left) + _text(right)
        if operator in _Parser.COMPARISONS:
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                pair = (left, right)
            else:
                pair = (_text(left).lower(), _text(right).lower())
            return {"=": pair[0] == pair[1], "<>": pair[0] != pair[1], "<": pair[0] < pair[1],
                    ">": pair[0] > pair[1], "<=": pair[0] <= pair[1], ">=": pair[0] >= pair[1]}[operator]
        a, b = _number(left), _number(right)
        if operator == "+":
            return a + b
        if operator == "-":
            return a - b
        if operator == "*":
            return a * b
        if operator == "/":
            return a / _divide_check(b)
        if operator == "^":
            try:
                return a ** b
            except (OverflowError, ZeroDivisionError):
                raise FormulaError("#VALUE!", "power out of range") from None
    raise FormulaError("#ERROR!", f"can't evaluate {kind}")


class _NoCells:
    def cell(self, row: int, col: int):
        raise FormulaError("#REF!", "no cells here")

    def cells(self, *_bounds):
        raise FormulaError("#REF!", "no cells here")


def evaluate_expression(text: str):
    """Evaluate a formula with no cell references; numbers come back as int when whole."""
    try:
        value = evaluate(parse(text), _NoCells())
    except FormulaError as error:
        raise ValueError(str(error)) from None
    if isinstance(value, complex):
        raise ValueError("the result is not a real number")
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value
# --- END [2.2] ---
