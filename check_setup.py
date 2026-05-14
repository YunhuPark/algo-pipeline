"""Step 1 셋업 검증 스크립트 — API 키 없이도 구조 확인 가능"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OK = "[OK]"
NG = "[!!]"

print("=" * 50)
print("  카드뉴스 파이프라인 Step 1 셋업 검증")
print("=" * 50)

# 1. Python 버전
print(f"\n[1] Python: {sys.version.split()[0]}", "✓" if sys.version_info >= (3, 10) else "✗ 3.10+ 필요")

# 2. 패키지 임포트
packages = {
    "openai": "openai",
    "langchain": "langchain",
    "langchain_openai": "langchain-openai",
    "tavily": "tavily",
    "PIL": "pillow",
    "dotenv": "python-dotenv",
    "pydantic": "pydantic",
}
print("\n[2] 패키지 임포트:")
for module, pkg in packages.items():
    try:
        __import__(module)
        print(f"    ✓ {pkg}")
    except ImportError:
        print(f"    ✗ {pkg} — pip install {pkg}")

# 3. 디렉터리 구조
print("\n[3] 디렉터리 구조:")
root = Path(__file__).parent
dirs = ["src/agents", "src/schemas", "src/utils", "templates", "fonts", "data", "output"]
for d in dirs:
    p = root / d
    status = "✓" if p.exists() else "✗ 없음"
    print(f"    {status}  {d}/")

# 4. 핵심 파일
print("\n[4] 핵심 파일:")
files = [
    "src/config.py",
    "src/schemas/card_news.py",
    "src/utils/text_utils.py",
    "src/pipeline.py",
    "main.py",
    "requirements.txt",
    ".env.example",
]
for f in files:
    p = root / f
    status = "✓" if p.exists() else "✗ 없음"
    print(f"    {status}  {f}")

# 5. .env 파일 + API 키 확인
print("\n[5] .env 설정:")
env_path = root / ".env"
if not env_path.exists():
    print("    ✗  .env 파일 없음")
    print("       → .env.example을 복사해서 .env를 만들고 API 키를 입력하세요:")
    print("         copy .env.example .env")
else:
    from dotenv import dotenv_values
    env = dotenv_values(str(env_path))
    for key in ["OPENAI_API_KEY", "TAVILY_API_KEY"]:
        val = env.get(key, "")
        if val and not val.endswith("..."):
            print(f"    ✓  {key} 설정됨")
        else:
            print(f"    ✗  {key} 미설정 — .env에 실제 키를 입력하세요")

# 6. Pydantic 스키마 로드 테스트
print("\n[6] 스키마 로드:")
try:
    sys.path.insert(0, str(root))
    from src.schemas.card_news import Slide, CardNewsScript, TrendReport
    print("    ✓ CardNewsScript, Slide, TrendReport")
except Exception as e:
    print(f"    ✗ {e}")

print("\n" + "=" * 50)
print("  Step 1 완료! API 키 설정 후 Step 2로 진행하세요.")
print("=" * 50 + "\n")
