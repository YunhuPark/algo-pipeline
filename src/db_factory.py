import sqlite3
import os
import sys
from pathlib import Path

# Connection Factory for Algorithm Pipeline
# Ensure that tests or synthetic runs DO NOT pollute the production database.

_test_connections = []
_allow_ro_in_test = False

def get_test_connections():
    return _test_connections

def reset_test_connections():
    _test_connections.clear()

def _get_env_vars():
    algo_env = os.getenv("ALGO_ENV")
    if not algo_env:
        raise ValueError("ALGO_ENV environment variable is missing. Must be set to 'production', 'development', 'test', or 'synthetic'.")

    algo_env = algo_env.lower()
    valid_envs = {"production", "development", "test", "synthetic"}
    if algo_env not in valid_envs:
        raise ValueError(f"Unknown ALGO_ENV: '{algo_env}'. Must be one of {valid_envs}.")

    allow_persistent = os.getenv("ALLOW_PERSISTENT_DB", "false").lower() == "true"
    return algo_env, allow_persistent

class DatabaseContaminationError(Exception):
    pass

def get_connection(db_path: str | Path, **kwargs) -> sqlite3.Connection:
    """
    Factory function for all SQLite connections.
    Prevents tests and synthetic scripts from modifying production-like databases unless explicitly allowed.
    """
    try:
        resolved_path = Path(db_path).resolve()
        db_name = resolved_path.name.lower()

        project_root = Path(__file__).resolve().parent.parent
        prod_tracking = (project_root / "data" / "tracking.db").resolve()
        prod_algo = (project_root / "data" / "algo.db").resolve()

        is_prod_db = False
        if resolved_path == prod_tracking or resolved_path == prod_algo:
            is_prod_db = True
        elif db_path == ":memory:":
            is_prod_db = False
        elif db_name in ("algo.db", "tracking.db"):
            if resolved_path.parent == project_root or resolved_path.parent == project_root / "data":
                is_prod_db = True
    except Exception as e:
        print(f"Exception in get_connection resolve: {e}")
        db_name = os.path.basename(str(db_path)).lower()
        is_prod_db = db_name in ("algo.db", "tracking.db")

    algo_env, allow_persistent = _get_env_vars()
    if os.environ.get("PYTEST_CURRENT_TEST") and "test_db_contamination_blocked" in os.environ.get("PYTEST_CURRENT_TEST", ""):
        print(f"DEBUG_ALWAYS: is_prod_db={is_prod_db}, allow_persistent={allow_persistent}, algo_env={algo_env}, resolved_path={resolved_path}, prod_tracking={prod_tracking}")


    if algo_env in ("test", "synthetic"):
        if is_prod_db and not allow_persistent:
            raise DatabaseContaminationError(
                f"[{algo_env}] Attempted to access production DB '{db_name}'. "
                "Use an in-memory DB or provide a distinct path for tests."
            )
    elif algo_env in ("production", "development"):
        if not is_prod_db and str(db_path) != ":memory:" and not allow_persistent:
            raise DatabaseContaminationError(
                f"[{algo_env}] Attempted to access non-production DB '{db_path}'. "
                "Production/Development must use the official persistent paths."
            )
        if algo_env == "test":
            uri = kwargs.get('uri', False)
            path_str = str(db_path)
            if uri and 'mode=ro' in path_str and not _allow_ro_in_test:
                raise DatabaseContaminationError("mode=ro access to prod DB is blocked in tests.")

            # Track the connection
            if str(db_path) != ":memory:":
                _test_connections.append(str(resolved_path))

    kwargs.setdefault("timeout", 30)
    conn = sqlite3.connect(str(db_path), **kwargs)
    conn.row_factory = sqlite3.Row

    # Block writing synth_run_* IDs to real DB
    def authorizer_callback(action, arg1, arg2, dbname, source):
        # SQLITE_INSERT = 9
        if action == 9 and is_prod_db and not allow_persistent:
            # We can't inspect the full bind variables easily in authorizer, but we can stop any INSERT
            # if we are strictly in a synthetic run on a real db unless allowed.
            if algo_env == "synthetic":
                raise DatabaseContaminationError("Insertion to persistent DB in synthetic mode is forbidden.")
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer_callback)

    return conn
