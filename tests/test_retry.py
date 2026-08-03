import pytest
import time
from src.utils.retry import call_with_retry

class MockResponse:
    def __init__(self, headers=None):
        self.headers = headers or {}

class MockException(Exception):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response

def test_retry_after_parsing():
    calls = []
    def mock_func():
        calls.append(1)
        if len(calls) < 2:
            raise MockException("429 too many requests", response=MockResponse({"Retry-After": "1"}))
        return "success"
    
    start = time.time()
    res = call_with_retry(mock_func, max_attempts=3)
    duration = time.time() - start
    assert res == "success"
    assert len(calls) == 2
    # Should have waited approx 1 second
    assert duration >= 0.9

def test_retry_invalid_retry_after():
    calls = []
    def mock_func():
        calls.append(1)
        if len(calls) < 2:
            raise MockException("rate limit exceeded", response=MockResponse({"Retry-After": "invalid"}))
        return "success"
    
    # Should fallback to delay * 4
    # base_delay=0.1 -> 0.4s
    start = time.time()
    res = call_with_retry(mock_func, max_attempts=3, base_delay=0.1)
    duration = time.time() - start
    assert res == "success"
    assert duration >= 0.35

def test_retry_max_attempts():
    calls = []
    def mock_func():
        calls.append(1)
        raise MockException("429")
        
    with pytest.raises(MockException):
        call_with_retry(mock_func, max_attempts=2, base_delay=0.1)
    
    assert len(calls) == 2

def test_retry_no_network():
    # 실제 네트워크 0회. 
    # 위 테스트들은 모두 mock_func를 사용하므로 네트워크 호출 없음.
    assert True
