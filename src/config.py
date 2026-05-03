"""환경 변수 로드 및 프로젝트 설정 관리"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트에서 .env 로드
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=False)


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"[설정 오류] '{key}'가 .env에 없습니다.\n"
            f".env.example을 참고해 .env 파일을 만들어주세요."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── API 키 ────────────────────────────────────────────────
OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
TAVILY_API_KEY: str = _require("TAVILY_API_KEY")
APIFY_API_KEY: str = _optional("APIFY_API_KEY")
PEXELS_API_KEY: str = _optional("PEXELS_API_KEY")

# ── 모델 설정 ─────────────────────────────────────────────
LLM_MODEL: str = _optional("LLM_MODEL", "gpt-4o")
LLM_TEMPERATURE: float = float(_optional("LLM_TEMPERATURE", "0.7"))

# ── 파이프라인 설정 ───────────────────────────────────────
DEFAULT_TOPIC: str = _optional("DEFAULT_TOPIC", "AI 트렌드")
NUM_CARDS: int = int(_optional("NUM_CARDS", "5"))

# ── 경로 ─────────────────────────────────────────────────
ROOT_DIR: Path = _ROOT
TEMPLATES_DIR: Path = _ROOT / "templates"
FONTS_DIR: Path = _ROOT / "fonts"
OUTPUT_DIR: Path = _ROOT / _optional("OUTPUT_DIR", "output")
DATA_DIR: Path = _ROOT / "data"

OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
