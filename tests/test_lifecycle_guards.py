from unittest.mock import MagicMock, patch

import pytest

from src.agent_control.state_machine import (
    AgentStateMachine,
    RunState,
    StateTransitionError,
)
from src.lifecycle import run_agent_lifecycle


def test_state_machine_rejects_invalid_terminal_transition():
    machine = AgentStateMachine()
    machine.transition(RunState.RUNNING)
    machine.transition(RunState.SUCCEEDED)

    with pytest.raises(StateTransitionError, match="Invalid run transition"):
        machine.transition(RunState.RUNNING)

    assert machine.terminal is True


def test_lifecycle_stops_after_first_failed_step():
    calls: list[str] = []

    def first():
        calls.append("first")
        return "ok"

    def failed():
        calls.append("failed")
        raise RuntimeError("stop")

    skipped = MagicMock()
    result = run_agent_lifecycle(
        [("first", first), ("failed", failed), ("skipped", skipped)]
    )

    assert result.state == RunState.FAILED
    assert result.failed_step == "failed"
    assert [step.succeeded for step in result.steps] == [True, False]
    assert result.steps[-1].error_type == "RuntimeError"
    assert calls == ["first", "failed"]
    skipped.assert_not_called()


def test_lifecycle_has_no_implicit_publisher_or_network_call():
    with patch("src.agents.publisher.publish") as publisher, patch(
        "httpx.Client.request"
    ) as http_request:
        result = run_agent_lifecycle([("local", lambda: "done")])

    assert result.state == RunState.SUCCEEDED
    publisher.assert_not_called()
    http_request.assert_not_called()
