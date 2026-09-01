from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from src.agent_control.state_machine import AgentStateMachine, RunState


@dataclass(frozen=True)
class LifecycleStepResult:
    name: str
    succeeded: bool
    value: Any = None
    error_type: str | None = None


@dataclass(frozen=True)
class LifecycleResult:
    state: RunState
    steps: tuple[LifecycleStepResult, ...] = field(default_factory=tuple)
    failed_step: str | None = None


def run_agent_lifecycle(
    steps: Iterable[tuple[str, Callable[[], Any]]],
    *,
    state_machine: AgentStateMachine | None = None,
) -> LifecycleResult:
    """Run injected local steps in order and stop at the first exception.

    This coordinator deliberately has no implicit DB, HTTP, publisher, policy
    activation, or retry behavior. Those effects must be provided as reviewed
    callables by a higher-level boundary.
    """

    machine = state_machine or AgentStateMachine()
    machine.transition(RunState.RUNNING)
    results: list[LifecycleStepResult] = []

    for name, step in steps:
        if not name.strip() or not callable(step):
            machine.transition(RunState.FAILED)
            return LifecycleResult(
                state=machine.state,
                steps=tuple(results),
                failed_step=name or None,
            )
        try:
            value = step()
        except Exception as exc:
            results.append(
                LifecycleStepResult(
                    name=name,
                    succeeded=False,
                    error_type=type(exc).__name__,
                )
            )
            machine.transition(RunState.FAILED)
            return LifecycleResult(
                state=machine.state,
                steps=tuple(results),
                failed_step=name,
            )
        results.append(LifecycleStepResult(name=name, succeeded=True, value=value))

    machine.transition(RunState.SUCCEEDED)
    return LifecycleResult(state=machine.state, steps=tuple(results))

