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
    return (
        os.getenv("ALGO_ENV", "development"),
        os.getenv("ALLOW_PERSISTENT_DB", "false").lower() == "true"
    )

class DatabaseContaminationError(Exception):
    pass

def get_connection(db_path: str | Path, **kwargs) -> sqlite3.Connection:
    """
    Factory function for all SQLite connections.
    Prevents tests and synthetic scripts from modifying production-like databases unless explicitly allowed.
    """
    import sys
    from pathlib import Path
    with open("C:/projects/cardnews/connection_trace.txt", "a", encoding="utf-8") as f:
        f.write(f"CONNECT_TRACE:{Path(db_path).resolve()}\n")
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
    
    if algo_env in ("test", "synthetic"):
        if is_prod_db and not allow_persistent:
            raise DatabaseContaminationError(
                f"[{algo_env}] Attempted to access production DB '{db_name}'. "
                "Use an in-memory DB or provide a distinct path for tests."
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
