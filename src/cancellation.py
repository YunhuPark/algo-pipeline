"""Cooperative cancellation for a running card-set generation.

A generation runs in a background thread and makes dozens of paid LLM calls. A
Python thread cannot be killed from outside, so "stop" is a request: the flag
is checked right before every LLM call (the one choke point they all pass
through, see llm_provider) and at a few phase boundaries. Setting it stops all
*future* spending at once; a call already in flight still finishes and is billed.

GenerationCancelled derives from BaseException on purpose. The pipeline has many
``except Exception`` blocks that log and carry on; if cancellation were an
Exception they would swallow it and the run would keep going. As a
BaseException it passes through them, while ``finally`` blocks (releasing the
queue row, the generation lock) still run.
"""
from __future__ import annotations

import threading


class GenerationCancelled(BaseException):
    """Raised inside a generation when the operator asked it to stop."""


_requested = threading.Event()


def request_cancel() -> None:
    _requested.set()


def reset() -> None:
    _requested.clear()


def is_requested() -> bool:
    return _requested.is_set()


def raise_if_requested() -> None:
    if _requested.is_set():
        raise GenerationCancelled("generation stopped by the operator")
