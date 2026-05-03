"""
Persona 시스템 — persona.json 로드 및 파이프라인 전체에 브랜드 설정 주입
주제별 자동 페르소나 오버라이드(resolve_persona) 포함
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass, field

_PERSONA_PATH = Path(__file__).parent.parent / "persona.json"


@dataclass
class Persona:
    # 브랜드
    brand_name: str = "CardNews AI"
    handle: str = "@cardnews_ai"
    tagline: str = ""
    description: str = ""

    # 보이스
    tone: str = "전문적이지만 친근한"
    style: str = "쉽고 직관적인"
    cta_text: str = "팔로우하면 최신 트렌드를 먼저 받아요!"
    target_audience: str = "IT 트렌드에 관심 있는 사람들"

    # 디자인
    primary_color: str = "#7B2FBE"
    accent_color: str = "#00D4FF"
    overlay_darkness: int = 175

    # 해시태그
    hashtag_base: list[str] = field(default_factory=lambda: ["#카드뉴스", "#트렌드", "#AI"])

    # 카드
    default_count: int = 6
    cover_emoji: str = "🤖"
    cta_emoji: str = "✨"

    # 감지된 카테고리 (주제별 자동 선택)
    topic_category: str = ""


def load_persona() -> Persona:
    """persona.json을 읽어 Persona 객체 반환. 파일 없으면 기본값 사용."""
    if not _PERSONA_PATH.exists():
        return Persona()

    try:
        data = json.loads(_PERSONA_PATH.read_text(encoding="utf-8"))
        return Persona(
            brand_name       = data["brand"]["name"],
            handle           = data["brand"]["handle"],
            tagline          = data["brand"].get("tagline", ""),
            description      = data["brand"].get("description", ""),
            tone             = data["voice"]["tone"],
            style            = data["voice"]["style"],
            cta_text         = data["voice"]["cta_text"],
            target_audience  = data["voice"]["target_audience"],
            primary_color    = data["design"]["primary_color"],
            accent_color     = data["design"]["accent_color"],
            overlay_darkness = data["design"]["overlay_darkness"],
            hashtag_base     = data.get("hashtag_base", []),
            default_count    = data["cards"]["default_count"],
            cover_emoji      = data["cards"]["cover_emoji"],
            cta_emoji        = data["cards"]["cta_emoji"],
        )
    except Exception as e:
        print(f"  [Persona] persona.json 로드 실패 ({e}), 기본값 사용")
        return Persona()


# ── 카테고리 정의 ─────────────────────────────────────────────────────────────

_CATEGORY_MAP: list[dict] = [
    {
        "name": "AI/LLM",
        "emoji": "🤖",
        "keywords": [
            "ai", "gpt", "claude", "gemini", "llm", "llama", "mistral", "grok",
            "openai", "anthropic", "deepmind", "huggingface", "diffusion", "sora",
            "midjourney", "runway", "생성형", "인공지능", "대형언어모델", "에이전트",
            "딥러닝", "머신러닝", "neural", "transformer", "chatbot", "챗봇",
            "perplexity", "copilot", "agent", "multimodal", "멀티모달",
        ],
        "voice": {
            "tone": "개발자와 크리에이터를 위한 열정적인 AI 얼리어답터 친구",
            "style": "기술 스펙보다 '오늘부터 내가 써먹을 수 있는 것'에 집중. '드디어', '이제', '진짜' 같은 생생한 구어체. 기술 장벽 없이 핵심만",
            "target_audience": "AI 도구를 적극 활용하는 개발자, 크리에이터, 얼리어답터",
        },
        "design": {"primary_color": "#5B4FE8", "accent_color": "#00E5FF"},
        "cards": {"cover_emoji": "🤖", "cta_emoji": "🔔"},
        "extra_hashtags": ["#AI", "#인공지능", "#ChatGPT", "#LLM", "#AI도구", "#머신러닝"],
    },
    {
        "name": "금융/코인",
        "emoji": "📈",
        "keywords": [
            "bitcoin", "비트코인", "ethereum", "이더리움", "crypto", "코인", "cryptocurrency",
            "주식", "stock", "nasdaq", "kospi", "코스피", "etf", "금리", "interest rate",
            "fed", "연준", "환율", "달러", "원화", "투자", "invest", "펀드", "채권", "bond",
            "부동산", "real estate", "경제지표", "gdp", "인플레이션", "inflation",
            "블록체인", "blockchain", "defi", "nft", "binance", "upbit", "업비트",
        ],
        "voice": {
            "tone": "냉정하고 신뢰감 있는 금융 전문 애널리스트",
            "style": "수치와 팩트 중심. '%', '억 달러', '기준금리' 같은 구체적 데이터로 설득. 추측과 과장 절대 금지. 투자자가 놓쳐선 안 될 핵심만",
            "target_audience": "주식·코인 투자자, 재테크에 관심 있는 2030 직장인",
        },
        "design": {"primary_color": "#00C896", "accent_color": "#FFD700"},
        "cards": {"cover_emoji": "📈", "cta_emoji": "💰"},
        "extra_hashtags": ["#주식", "#코인", "#비트코인", "#투자", "#재테크", "#금융", "#ETF"],
    },
    {
        "name": "비즈니스/스타트업",
        "emoji": "💼",
        "keywords": [
            "스타트업", "startup", "창업", "vc", "벤처캐피탈", "series a", "series b",
            "펀딩", "funding", "유니콘", "unicorn", "ipo", "상장", "인수합병", "m&a",
            "기업가치", "valuation", "ceo", "경영", "전략", "매출", "영업이익",
            "비즈니스모델", "플랫폼", "saas", "b2b", "마케팅", "브랜딩",
        ],
        "voice": {
            "tone": "스타트업 씬에 빠삭한 비즈니스 인사이터",
            "style": "성공과 실패의 핵심 원인을 날카롭게 짚어주는 분석적 스타일. 창업자와 직장인이 '나도 적용해야겠다' 싶게. 숫자와 사례로 증명",
            "target_audience": "창업자, 스타트업 관계자, 비즈니스 트렌드에 관심 있는 직장인",
        },
        "design": {"primary_color": "#FF6B35", "accent_color": "#FFF176"},
        "cards": {"cover_emoji": "💼", "cta_emoji": "🚀"},
        "extra_hashtags": ["#스타트업", "#창업", "#비즈니스", "#투자", "#VC", "#기업가치"],
    },
    {
        "name": "IT/테크",
        "emoji": "💻",
        "keywords": [
            "apple", "애플", "samsung", "삼성", "google", "구글", "microsoft", "마이크로소프트",
            "meta", "amazon", "아마존", "iphone", "아이폰", "android", "안드로이드",
            "galaxy", "갤럭시", "chip", "칩", "semiconductor", "반도체", "nvidia", "엔비디아",
            "software", "소프트웨어", "app", "앱", "os", "운영체제", "cloud", "클라우드",
            "aws", "azure", "cybersecurity", "보안", "해킹", "5g", "6g", "internet",
            "wwdc", "i/o", "개발자컨퍼런스", "launch", "출시", "release",
        ],
        "voice": {
            "tone": "실용적인 테크 제품 전문 리뷰어",
            "style": "이 기기·앱·서비스가 내 일상을 어떻게 바꾸는지 구체적 시나리오로 설명. 전문용어를 쉽게 풀어주는 친절한 가이드. '이걸 쓰면 이렇게 편해져요'",
            "target_audience": "최신 기기와 앱을 즐겨 쓰는 테크 유저, 얼리어답터",
        },
        "design": {"primary_color": "#0078D4", "accent_color": "#00E5FF"},
        "cards": {"cover_emoji": "💻", "cta_emoji": "🔔"},
        "extra_hashtags": ["#테크", "#IT", "#애플", "#삼성", "#구글", "#앱", "#신제품"],
    },
    {
        "name": "사회/정치",
        "emoji": "🏛️",
        "keywords": [
            "대통령", "국회", "정부", "정책", "법안", "선거", "정치", "여당", "야당",
            "복지", "교육정책", "노동", "사회문제", "인구", "저출생", "고령화",
            "president", "government", "policy", "election", "congress", "parliament",
            "regulation", "규제", "세금", "tax", "법원", "판결", "대법원",
        ],
        "voice": {
            "tone": "균형 잡힌 시각의 사회 현안 큐레이터",
            "style": "편향 없이 핵심 팩트만 정확하게 전달. 여야 어느 편도 들지 않고 독자가 스스로 판단할 수 있게. '이렇게 됩니다'가 아닌 '이런 변화가 있습니다'로 서술",
            "target_audience": "사회 이슈에 관심 있는 MZ 유권자, 시사에 밝고 싶은 일반인",
        },
        "design": {"primary_color": "#E74C3C", "accent_color": "#ECF0F1"},
        "cards": {"cover_emoji": "🏛️", "cta_emoji": "🗳️"},
        "extra_hashtags": ["#사회이슈", "#정책", "#뉴스", "#시사", "#한국사회"],
    },
    {
        "name": "과학/우주",
        "emoji": "🔭",
        "keywords": [
            "nasa", "spacex", "space", "우주", "화성", "mars", "moon", "달", "rocket",
            "로켓", "위성", "satellite", "천문학", "astronomy", "양자", "quantum",
            "physics", "물리", "chemistry", "화학", "biology", "생물", "dna", "유전자",
            "의학", "medicine", "vaccine", "백신", "연구", "논문", "발견", "climate",
            "기후변화", "탄소", "carbon", "재생에너지", "renewable energy",
        ],
        "voice": {
            "tone": "복잡한 과학을 일상 언어로 번역해주는 열정적인 사이언스 커뮤니케이터",
            "style": "경이로움과 호기심을 자극하는 표현 + 어려운 개념을 일상적 비유로 쉽게 설명. '사실 이게 얼마나 대단한 건지 아세요?'라는 감탄에서 출발",
            "target_audience": "과학 콘텐츠가 좋지만 어렵게 느껴지던 일반인, 지식 유튜브 팬",
        },
        "design": {"primary_color": "#9B59B6", "accent_color": "#00E5FF"},
        "cards": {"cover_emoji": "🔭", "cta_emoji": "✨"},
        "extra_hashtags": ["#과학", "#우주", "#NASA", "#연구", "#발견", "#사이언스"],
    },
]

# 기본 (아무 카테고리도 안 맞을 때)
_DEFAULT_CATEGORY = {
    "name": "글로벌/트렌드",
    "emoji": "📰",
    "voice": None,  # persona.json 기본값 유지
    "design": None,
    "cards": None,
    "extra_hashtags": [],
}


def _detect_category(topic: str) -> dict:
    """주제 텍스트에서 키워드 매칭으로 카테고리 감지 (GPT 없이 빠르게)."""
    topic_lower = topic.lower()
    scores: list[tuple[int, dict]] = []
    for cat in _CATEGORY_MAP:
        hits = sum(1 for kw in cat["keywords"] if kw.lower() in topic_lower)
        if hits > 0:
            scores.append((hits, cat))
    if scores:
        scores.sort(key=lambda x: x[0], reverse=True)
        return scores[0][1]
    return _DEFAULT_CATEGORY


def resolve_persona(topic: str, base: Persona) -> Persona:
    """
    주제를 분석하여 적합한 페르소나를 자동으로 반환한다.
    브랜드(이름·핸들·해시태그 베이스)는 고정, voice·design·cards·extra_hashtags만 덮어씀.
    """
    cat = _detect_category(topic)
    p = deepcopy(base)
    p.topic_category = f"{cat['emoji']} {cat['name']}"

    # voice 덮어쓰기
    if cat.get("voice"):
        p.tone            = cat["voice"]["tone"]
        p.style           = cat["voice"]["style"]
        p.target_audience = cat["voice"]["target_audience"]

    # design 덮어쓰기
    if cat.get("design"):
        p.primary_color = cat["design"]["primary_color"]
        p.accent_color  = cat["design"]["accent_color"]

    # cards 덮어쓰기
    if cat.get("cards"):
        p.cover_emoji = cat["cards"]["cover_emoji"]
        p.cta_emoji   = cat["cards"]["cta_emoji"]

    # 해시태그: 기존 base 해시태그에 카테고리 특화 태그 추가 (중복 제거)
    extra = cat.get("extra_hashtags", [])
    existing = set(p.hashtag_base)
    for ht in extra:
        if ht not in existing:
            p.hashtag_base.append(ht)
            existing.add(ht)

    print(f"  [PersonaRouter] 카테고리: {p.topic_category} → 색상 {p.primary_color} / 이모지 {p.cover_emoji}")
    return p
