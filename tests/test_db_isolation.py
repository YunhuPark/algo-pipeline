import pytest
import os
from pathlib import Path
from src.db_factory import get_connection, DatabaseContaminationError

def test_db_isolation_blocks_prod_db(monkeypatch):
    monkeypatch.setenv("ALGO_ENV", "test")
    
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/tracking.db")
        
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/algo.db")
        
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection(Path("data/algo.db").resolve())
        
    # Case insensitivity test
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/TRACKING.DB")
        
def test_db_isolation_allows_test_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    test_db = tmp_path / "test_tracking.db"
    
    conn = get_connection(test_db)
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INT)")

    conn.commit()
    conn.close()
    
    assert test_db.exists()
    
    prod_tracking = Path("data/tracking.db").resolve()
    assert test_db.resolve() != prod_tracking

def test_db_isolation_path_edge_cases(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    
    tmp_path_with_data = tmp_path / "data_folder"
    tmp_path_with_data.mkdir()
    test_db_1 = tmp_path_with_data / "tracking.db"
    conn = get_connection(test_db_1)
    conn.close()
    
    with pytest.raises(DatabaseContaminationError):
        get_connection("data/tracking.db")
        
    with pytest.raises(DatabaseContaminationError):
        get_connection("data/algo.db")
        
    with pytest.raises(DatabaseContaminationError):
        get_connection("./data/tracking.db")
        
    mydata_dir = tmp_path / "mydata"
    mydata_dir.mkdir()
    test_db_2 = mydata_dir / "algo.db"
    conn2 = get_connection(test_db_2)
    conn2.close()
    
def test_data_in_test_name(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    test_db = tmp_path / "algo.db"
    conn = get_connection(test_db)
    conn.close()

import sys
from unittest.mock import patch

def test_import_side_effects():
    with patch('sqlite3.connect') as mock_connect:
        import src.db
        import src.pipeline
        mock_connect.assert_not_called()

def test_explicit_init_db(monkeypatch, tmp_path):
    import os
    monkeypatch.setenv("ALGO_ENV", "test")
    test_db = tmp_path / "algo.db"
    monkeypatch.setenv("ALGO_DB_PATH", str(test_db))
    
    from src.db import init_db
    init_db()
    
    conn = get_connection(test_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'")
    assert cursor.fetchone() is not None
    init_db()
    conn.close()
        
def test_all_connections_factory(monkeypatch, tmp_path):
    import os
    monkeypatch.setenv("ALGO_ENV", "test")
    test_db = tmp_path / "algo.db"
    monkeypatch.setenv("ALGO_DB_PATH", str(test_db))

    from src.db import init_db, insert_post
    init_db()
    insert_post('instagram', 'Test Topic', 'post_123')
    conn = get_connection(test_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM posts WHERE post_id='post_123'")
    assert cursor.fetchone() is not None
    conn.close()
