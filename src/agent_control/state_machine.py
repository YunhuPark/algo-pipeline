from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


VALID_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.PAUSED, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.PAUSED: frozenset(
        {RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


class StateTransitionError(ValueError):
    pass


@dataclass
class AgentStateMachine:
    """In-memory state machine with no DB, network, or publisher side effects."""

    state: RunState = RunState.PENDING
    history: list[RunState] = field(default_factory=lambda: [RunState.PENDING])

    def transition(self, target: RunState | str) -> RunState:
        try:
            target_state = target if isinstance(target, RunState) else RunState(target)
        except ValueError as exc:
            raise StateTransitionError(f"Unknown run state: {target}") from exc

        if target_state not in VALID_TRANSITIONS[self.state]:
            raise StateTransitionError(
                f"Invalid run transition: {self.state.value} -> {target_state.value}"
            )
        self.state = target_state
        self.history.append(target_state)
        return self.state

    @property
    def terminal(self) -> bool:
        return not VALID_TRANSITIONS[self.state]
