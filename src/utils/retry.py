"""지수 백오프 재시도 유틸리티"""
from __future__ import annotations

import random
import time
from typing import Any, Callable


def call_with_retry(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    backoff: float = 2.0,
    label: str = "",
    **kwargs,
) -> Any:
    """
    지수 백오프 재시도: 레이트리밋(429/quota) 감지 시 대기를 길게 함.
    EnvironmentError(토큰 만료 등 치명적 오류)는 즉시 재발생.

    Args:
        func:         호출할 함수
        *args:        함수 위치 인자
        max_attempts: 최대 시도 횟수 (기본 3)
        base_delay:   초기 대기 시간(초) (기본 2)
        max_delay:    최대 대기 시간(초) (기본 120)
        backoff:      대기 시간 증가 배율 (기본 2)
        label:        로그용 이름 (없으면 func.__name__ 사용)
        **kwargs:     함수 키워드 인자
    """
    delay = base_delay
    name = label or getattr(func, "__name__", "call")

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except EnvironmentError:
            raise  # 치명적 오류(토큰 만료 등)는 재시도 불가
        except Exception as e:
            if attempt == max_attempts:
                raise

            err_str = str(e).lower()
            # 크레딧 부족 등 재시도가 해결할 수 없는 오류는 즉시 재발생
            if "insufficient_quota" in err_str or "billing" in err_str:
                print(f"  [retry] {name} OpenAI 크레딧 부족으로 재시도 불가, 즉시 종료")
                raise

            is_rate_limit = any(kw in err_str for kw in ("rate", "429", "too many requests"))

            # Extract Retry-After if available in exception
            retry_after = None
            if hasattr(e, "response") and hasattr(e.response, "headers"):
                retry_after_str = e.response.headers.get("Retry-After")
                if retry_after_str:
                    try:
                        retry_after = int(retry_after_str)
                    except ValueError:
                        pass

            if is_rate_limit:
                if retry_after:
                    wait = min(retry_after, max_delay)
                else:
                    wait = min(delay * 4, max_delay)
                print(f"  [retry] {name} 레이트리밋(시도 {attempt}): {wait:.0f}s 대기..")
            else:
                jitter = random.uniform(0, delay * 0.2)
                wait = min(delay + jitter, max_delay)
                print(f"  [retry] {name} 오류 (시도 {attempt}/{max_attempts}): {type(e).__name__} -> {wait:.1f}s 후 재시도")

            time.sleep(wait)
            delay = min(delay * backoff, max_delay)
