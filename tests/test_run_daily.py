import pytest
import sys
import io
from scripts.run_daily import configure_console_encoding
from unittest.mock import patch, MagicMock

def test_configure_console_encoding_safe_for_pytest(capsys):
    """
    pytest의 전역 capture 객체를 닫거나 교체하지 않음을 확인.
    capsys 픽스처가 활성화된 상태에서 함수가 실행되어도, print 캡처가 정상 동작해야 함.
    """
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    # 캡처 스트림에 대해 실행
    configure_console_encoding()
    
    # pytest capture 객체가 그대로 유지되어야 함
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    
    print("Test stdout output")
    sys.stderr.write("Test stderr output\n")
    
    captured = capsys.readouterr()
    assert "Test stdout output" in captured.out
    assert "Test stderr output" in captured.err

def test_configure_console_encoding_reconfigure():
    """reconfigure()가 있는 경우 이를 호출하는지 확인."""
    mock_stdout = MagicMock()
    mock_stdout.reconfigure = MagicMock()
    
    with patch("sys.stdout", mock_stdout), patch("sys.stderr", MagicMock()):
        configure_console_encoding()
        
    mock_stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

def test_configure_console_encoding_fallback():
    """reconfigure()가 없고 buffer가 있는 경우 래퍼로 교체되는지 확인."""
    class MockStream:
        def __init__(self):
            self.buffer = io.BytesIO()
            self.encoding = "cp949"
            
    mock_stdout = MockStream()
    mock_stderr = MockStream()
    
    with patch("sys.stdout", mock_stdout), patch("sys.stderr", mock_stderr):
        configure_console_encoding()
        assert isinstance(sys.stdout, io.TextIOWrapper)
        assert sys.stdout.encoding == "utf-8"
        assert sys.stdout.errors == "replace"
        assert isinstance(sys.stderr, io.TextIOWrapper)
        assert sys.stderr.encoding == "utf-8"
        assert sys.stderr.errors == "replace"
