"""The `ALGO_ENV=test` handling in `get_connection` (connection tracking and the `mode=ro` URI guard).

That block was indented INSIDE the `production/development` branch (introduced in 18f79fc), where `algo_env == "test"` can never be true, so it never ran:
`get_test_connections()` was always empty and read-only URIs were never blocked in tests. These tests exercise the block where it is meant to run
(only when ALGO_ENV is `test`) and pin that the other environments are not affected.
"""

import sqlite3

import pytest

from src import db_factory
from src.db_factory import DatabaseContaminationError, get_connection, get_test_connections, reset_test_connections


@pytest.fixture(autouse=True)
def clean_tracking():
    reset_test_connections()
    yield
    reset_test_connections()


def test_in_the_test_environment_file_connections_are_tracked_and_memory_ones_are_not(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db = tmp_path / "tracked.db"
    get_connection(db).close()
    get_connection(":memory:").close()
    assert get_test_connections() == [str(db.resolve())]


def test_reset_clears_the_tracked_connections(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    get_connection(tmp_path / "a.db").close()
    assert get_test_connections()
    reset_test_connections()
    assert get_test_connections() == []


def test_in_the_test_environment_a_read_only_uri_is_blocked_unless_explicitly_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    db = tmp_path / "ro.db"
    sqlite3.connect(str(db)).close()  # the file has to exist for a read-only open
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    with pytest.raises(DatabaseContaminationError, match="mode=ro"):
        get_connection(uri, uri=True)
    monkeypatch.setattr(db_factory, "_allow_ro_in_test", True)
    get_connection(uri, uri=True).close()


@pytest.mark.parametrize("env", ["development", "synthetic"])
def test_other_environments_are_not_subject_to_the_test_only_rules(monkeypatch, tmp_path, env):
    monkeypatch.setenv("ALGO_ENV", env)
    monkeypatch.setenv("ALLOW_PERSISTENT_DB", "true")  # development refuses non-production paths otherwise
    db = tmp_path / "other.db"
    get_connection(db).close()
    assert get_test_connections() == []  # tracking is for ALGO_ENV=test only
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    get_connection(uri, uri=True).close()  # and a read-only URI is not blocked here
