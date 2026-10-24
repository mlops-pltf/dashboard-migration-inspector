"""A tiny token stream + precedence-climbing helper shared by the formula parsers."""

from __future__ import annotations

from dataclasses import dataclass


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    kind: str  # NUM STR IDENT REF BRACE OP LP RP LB RB COMMA PARAMS EOF
    text: str
    pos: int


class Stream:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.i = 0
        self.source = source

    def peek(self, k: int = 0) -> Token:
        return self.tokens[min(self.i + k, len(self.tokens) - 1)]

    def next(self) -> Token:
        t = self.peek()
        self.i += 1
        return t

    def at(self, kind: str, text: str | None = None, k: int = 0) -> bool:
        t = self.peek(k)
        return t.kind == kind and (text is None or t.text.lower() == text)

    def accept(self, kind: str, text: str | None = None) -> Token | None:
        return self.next() if self.at(kind, text) else None

    def expect(self, kind: str, text: str | None = None) -> Token:
        if not self.at(kind, text):
            t = self.peek()
            raise ParseError(f"expected {text or kind} at {t.pos}, found {t.text!r} in {self.source!r}")
        return self.next()


BINARY_PRECEDENCE = {
    "or": 1, "and": 2,
    "=": 3, "!=": 3, "<>": 3, "<": 3, "<=": 3, ">": 3, ">=": 3,
    "+": 4, "-": 4, "||": 4,
    "*": 5, "/": 5, "%": 5,
}
