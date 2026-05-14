"""Claude tool_use 기반 카드뉴스 에이전트 오케스트레이터"""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from agent.prompts import SYSTEM_PROMPT
from agent.tools import TOOLS
from config.settings import Settings
from content.generator import generate_card_content
from content.models import CardNewsSet, RunResult
from research.keyword_analyzer import analyze
from research.news_scraper import get_recent_articles
from research.trend_scraper import get_weekly_trends
from renderer.card_renderer import render_card_set
from styles.style_manager import load_profile, select_style

MAX_ITERATIONS = 20


class CardNewsOrchestrator:
    def __init__(self, settings: Settings, handle: str = "", num_cards: int = 6, style_override: str | None = None):
        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.handle = handle or settings.account_handle
        self.num_cards = num_cards
        self.style_override = style_override
        self.output_paths: list[Path] = []

    # ─── 도구 핸들러 ────────────────────────────────────────────────────

    def _handle_research_trends(self, args: dict) -> dict:
        topic = args["topic"]
        timeframe = args.get("timeframe", "today 7-d")
        items = get_weekly_trends(topic, timeframe)
        return {"trends": items, "count": len(items)}

    def _handle_scrape_news(self, args: dict) -> dict:
        query = args["query"]
        max_results = args.get("max_results", 10)
        articles = get_recent_articles(query, max_results)
        return {"articles": articles, "count": len(articles)}

    def _handle_analyze_keywords(self, args: dict) -> dict:
        themes = analyze(
            trends_data=args.get("trends_data", []),
            news_data=args.get("news_data", []),
            max_themes=args.get("max_themes", 5),
        )
        return {"themes": themes}

    def _handle_select_style(self, args: dict) -> dict:
        profile = select_style(
            topic=args["topic"],
            mood=args.get("mood", "auto"),
            style_override=self.style_override or args.get("style_override"),
        )
        return {"style_name": profile.name, "display_name": profile.display_name}

    def _handle_generate_card_content(self, args: dict) -> dict:
        # persona.json에서 브랜드 정보 로드
        persona_path = Path("config/persona.json")
        persona = {}
        if persona_path.exists():
            import json as _json
            persona = _json.loads(persona_path.read_text(encoding="utf-8"))

        card_set = generate_card_content(
            topic=args["topic"],
            themes=args.get("themes", [args["topic"]]),
            style_profile=args.get("style_profile", self.settings.default_style),
            num_cards=args.get("num_cards", self.num_cards),
            brand_voice=args.get("brand_voice", persona.get("voice_tone", "전문적이지만 친근한")),
            trend_keywords=args.get("trend_keywords", []),
            api_key=self.settings.anthropic_api_key,
        )
        return card_set.model_dump(default=str)

    def _handle_render_cards(self, args: dict) -> dict:
        card_content = args["card_content"]
        output_dir = args.get("output_dir", str(self.settings.output_base_dir))

        card_set = CardNewsSet(**card_content)
        paths = render_card_set(
            card_news=card_set,
            output_base_dir=output_dir,
            handle=self.handle,
            width=self.settings.image_width,
            height=self.settings.image_height,
        )
        self.output_paths = paths
        return {
            "success": True,
            "files": [str(p) for p in paths],
            "count": len(paths),
            "output_dir": str(paths[0].parent) if paths else output_dir,
        }

    # ─── 도구 디스패처 ──────────────────────────────────────────────────

    def _execute_tool(self, name: str, inputs: dict) -> dict:
        handlers = {
            "research_trends": self._handle_research_trends,
            "scrape_news": self._handle_scrape_news,
            "analyze_keywords": self._handle_analyze_keywords,
            "select_style": self._handle_select_style,
            "generate_card_content": self._handle_generate_card_content,
            "render_cards": self._handle_render_cards,
        }
        handler = handlers.get(name)
        if not handler:
            return {"error": f"알 수 없는 도구: {name}"}
        try:
            return handler(inputs)
        except Exception as exc:
            return {"error": str(exc), "tool": name}

    # ─── 메인 에이전트 루프 ─────────────────────────────────────────────

    def run(self, user_input: str) -> RunResult:
        messages = [{"role": "user", "content": user_input}]
        iteration = 0
        final_message = ""

        while iteration < MAX_ITERATIONS:
            iteration += 1

            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            # 응답 메시지 추가
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # 텍스트 블록 수집
                for block in response.content:
                    if hasattr(block, "text"):
                        final_message = block.text
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                # 예상치 못한 stop_reason
                break

        if not self.output_paths:
            return RunResult(
                success=False,
                error="이미지 렌더링이 완료되지 않았습니다.",
                summary_message=final_message,
            )

        first_path = self.output_paths[0]
        return RunResult(
            success=True,
            output_paths=self.output_paths,
            topic=first_path.parent.name,
            num_cards=len(self.output_paths),
            style_used=self.style_override or self.settings.default_style,
            summary_message=final_message,
        )
