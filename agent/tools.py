"""Claude API에 전달할 tool 스키마 정의"""

TOOLS = [
    {
        "name": "research_trends",
        "description": "주어진 주제의 이번 주 한국 Google Trends 인기 키워드를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "검색할 주제 (예: 'AI', '환경', '경제')",
                },
                "timeframe": {
                    "type": "string",
                    "enum": ["today 7-d", "today 1-m"],
                    "description": "조회 기간. 기본값 today 7-d",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "scrape_news",
        "description": "네이버 뉴스에서 해당 주제의 최신 기사 제목과 요약을 가져옵니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색 쿼리",
                },
                "max_results": {
                    "type": "integer",
                    "description": "최대 기사 수 (기본 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "analyze_keywords",
        "description": "트렌드 데이터와 뉴스 데이터를 결합해 카드뉴스에 쓸 핵심 테마를 선별합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trends_data": {
                    "type": "array",
                    "description": "research_trends 결과",
                    "items": {"type": "object"},
                },
                "news_data": {
                    "type": "array",
                    "description": "scrape_news 결과",
                    "items": {"type": "object"},
                },
                "max_themes": {
                    "type": "integer",
                    "description": "추출할 테마 수 (기본 5)",
                    "default": 5,
                },
            },
            "required": ["trends_data", "news_data"],
        },
    },
    {
        "name": "select_style",
        "description": "주제와 분위기에 맞는 카드뉴스 시각 스타일을 선택합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "카드뉴스 주제",
                },
                "mood": {
                    "type": "string",
                    "enum": ["professional", "casual", "energetic", "soft", "dark", "auto"],
                    "description": "분위기",
                },
                "style_override": {
                    "type": "string",
                    "description": "특정 스타일 강제 지정 (옵션): bold_gradient | dark_modern | editorial | minimalist | pastel_soft",
                },
            },
            "required": ["topic", "mood"],
        },
    },
    {
        "name": "generate_card_content",
        "description": "카드뉴스의 각 슬라이드에 들어갈 한국어 카피(제목, 본문, 해시태그)를 생성합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "카드뉴스 주제",
                },
                "themes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "카드에 담을 핵심 테마 목록",
                },
                "style_profile": {
                    "type": "string",
                    "description": "사용할 스타일 이름",
                },
                "num_cards": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 10,
                    "description": "생성할 카드 수",
                },
                "brand_voice": {
                    "type": "string",
                    "description": "말투/톤 (예: '전문적이지만 친근한')",
                },
                "trend_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "포함할 트렌드 키워드",
                },
            },
            "required": ["topic", "themes", "style_profile", "num_cards"],
        },
    },
    {
        "name": "render_cards",
        "description": "생성된 카드뉴스 콘텐츠를 실제 PNG 이미지 파일(1080x1350px)로 렌더링합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_content": {
                    "type": "object",
                    "description": "generate_card_content의 반환값 (CardNewsSet JSON)",
                },
                "output_dir": {
                    "type": "string",
                    "description": "저장 디렉터리 (기본 'output')",
                    "default": "output",
                },
            },
            "required": ["card_content"],
        },
    },
]
