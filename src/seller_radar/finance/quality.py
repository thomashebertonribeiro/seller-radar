"""Estado de qualidade do dado, com o motivo — nunca só o rótulo."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    COMPLETE = "completo"
    ESTIMATED = "estimado"
    INCOMPLETE = "incompleto"


_SEVERITY = {State.COMPLETE: 0, State.ESTIMATED: 1, State.INCOMPLETE: 2}


@dataclass(frozen=True)
class Quality:
    state: State = State.COMPLETE
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def complete(cls) -> Quality:
        return cls()

    @classmethod
    def estimated(cls, *reasons: str) -> Quality:
        return cls(State.ESTIMATED, tuple(reasons))

    @classmethod
    def incomplete(cls, *reasons: str) -> Quality:
        return cls(State.INCOMPLETE, tuple(reasons))

    def merge(self, other: Quality) -> Quality:
        state = self.state if _SEVERITY[self.state] >= _SEVERITY[other.state] else other.state
        reasons = tuple(sorted(set(self.reasons) | set(other.reasons)))
        return Quality(state, reasons)

    @property
    def is_complete(self) -> bool:
        return self.state is State.COMPLETE
