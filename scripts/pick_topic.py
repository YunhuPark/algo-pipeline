"""
큐가 비어있을 때 GPT로 오늘의 주제를 선택해서 파이프라인 실행.
Tavily 없이 OpenAI만으로 동작.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def pick_topic() -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 한국 인스타그램 AI/테크 계정 @algo__kr의 콘텐츠 기획자입니다. "
                    "MZ세대가 흥미로워할 AI, 테크, 스타트업 관련 주제를 선정합니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"오늘은 {today}입니다. "
                    "최근 AI/테크 트렌드 중에서 인스타그램 카드뉴스로 만들기 좋은 주제 1개를 골라주세요. "
                    "주제만 짧게 (10자 이내) 답해주세요. 예: 'GPT-5의 충격', 'AI 에이전트 시대'"
                ),
            },
        ],
        max_tokens=30,
        temperature=0.9,
    )
    return resp.choices[0].message.content.strip().strip('"').strip("'")


def main() -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] GPT 주제 선택 중...")
    topic = pick_topic()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 선택된 주제: {topic}")

    cmd = [
        sys.executable, str(ROOT / "main.py"),
        topic,
        "--publish",
    ]
    result = subprocess.run(cmd, cwd=str(ROOT))
    sys.exit(result.returncode)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
