import pytest
import os
from pathlib import Path
from src.db_factory import get_connection, DatabaseContaminationError

def test_db_isolation_blocks_prod_db():
    os.environ["ALGO_ENV"] = "test"
    
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/tracking.db")
        
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/algo.db")
        
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection(Path("data/algo.db").resolve())
        
    # Case insensitivity test
    with pytest.raises(DatabaseContaminationError, match="Attempted to access production DB"):
        get_connection("data/TRACKING.DB")
        
    # Symlink bypass simulation (just checking the resolve name is lowercased and matched)
    # Since we can't always create symlinks on Windows without admin, we trust Path.resolve() logic tested above
    
def test_db_isolation_allows_test_db(tmp_path):
    os.environ["ALGO_ENV"] = "test"
    test_db = tmp_path / "test_tracking.db"
    
    conn = get_connection(test_db)
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INT)")

    conn.commit()
    conn.close()
    
    assert test_db.exists()
    assert str(tmp_path) in str(test_db.resolve())
    
    prod_tracking = Path("data/tracking.db").resolve()
    assert test_db.resolve() != prod_tracking

def test_db_isolation_path_edge_cases(tmp_path):
    os.environ["ALGO_ENV"] = "test"
    
    # tmp_path 이름에 data가 포함돼도 임시 DB는 허용
    tmp_path_with_data = tmp_path / "data_folder"
    tmp_path_with_data.mkdir()
    test_db_1 = tmp_path_with_data / "tracking.db"
    conn = get_connection(test_db_1)
    conn.close()
    
    # 실제 data/tracking.db 경로는 차단
    with pytest.raises(DatabaseContaminationError):
        get_connection("data/tracking.db")
        
    # 실제 data/algo.db 경로는 차단
    with pytest.raises(DatabaseContaminationError):
        get_connection("data/algo.db")
        
    # 상대경로로 운영 DB를 가리켜도 차단
    with pytest.raises(DatabaseContaminationError):
        get_connection("./data/tracking.db")
        
    # 경로 문자열 일부에만 data가 포함된 별도 임시 폴더는 허용
    mydata_dir = tmp_path / "mydata"
    mydata_dir.mkdir()
    test_db_2 = mydata_dir / "algo.db"
    conn2 = get_connection(test_db_2)
    conn2.close()
    
def test_data_in_test_name(tmp_path):
    """test 함수명에 data가 포함돼도 허용"""
    os.environ["ALGO_ENV"] = "test"
    test_db = tmp_path / "algo.db"
    conn = get_connection(test_db)
    conn.close()
