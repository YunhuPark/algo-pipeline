"""
Blog Publisher — 카드뉴스 → 블로그 포스팅 자동 변환 + 발행
────────────────────────────────────────────────────────
지원 플랫폼:
  1. Tistory  — REST API v1 (TISTORY_ACCESS_TOKEN 필요)
  2. 파일 저장 — 마크다운 파일로 로컬 저장 (API 키 없을 때 폴백)

사용:
    from src.agents.blog_publisher import publish

    result = publish(script, image_paths)
    print(result["platform"], result["url"])

환경 변수 (.env):
  TISTORY_ACCESS_TOKEN=...   (없으면 파일 저장 모드)
  TISTORY_BLOG_NAME=...      (예: myblog → myblog.tistory.com)
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from src.config import OPENAI_API_KEY, OUTPUT_DIR
from src.schemas.card_news import CardNewsScript

# 런타임에만 httpx 임포트 (Tistory 발행 시)
# from dotenv import load_dotenv — config.py에서 이미 로드됨


# ── 환경 변수 ─────────────────────────────────────────────
TISTORY_ACCESS_TOKEN: str = os.getenv("TISTORY_ACCESS_TOKEN", "")
TISTORY_BLOG_NAME: str    = os.getenv("TISTORY_BLOG_NAME", "")
TISTORY_API_BASE          = "https://www.tistory.com/apis"


# ── GPT 구조화 출력 스키마 ────────────────────────────────

class _BlogPost(BaseModel):
    title: str           # SEO 최적화된 블로그 제목
    intro: str           # 서론 (2~3 문단, 마크다운)
    sections: list[str]  # 각 슬라이드 → 확장된 본문 섹션 (마크다운)
    conclusion: str      # 결론 및 CTA (마크다운)
    meta_description: str  # SEO 메타 설명 (160자 이내)


# ── GPT 프롬프트 ──────────────────────────────────────────

_BLOG_SYSTEM = """
당신은 SEO 전문 블로그 작가입니다.
카드뉴스 스크립트를 받아 SEO 친화적인 블로그 포스팅으로 확장합니다.

작성 지침:
- 제목: 검색 의도를 반영한 명확한 제목 (숫자/연도 포함 권장)
- 서론: 독자의 궁금증을 자극하는 도입부 + 이 글에서 다룰 내용 요약
- 본문 섹션: 각 슬라이드 내용을 3~5줄로 확장. 소제목은 ## 사용
- 결론: 핵심 요약 + 다음 행동 유도 (CTA)
- 전체 분량: 800~1500자 목표
- 마크다운 형식: 소제목(##/###), 굵은 글씨(**), 목록(-) 적극 활용
- 말투: 친근하고 전문적인 블로그 톤 (독자를 '여러분'으로 호칭)
- meta_description: 핵심 키워드 포함, 160자 이내

반드시 한국어로 작성하세요.
"""

_BLOG_HUMAN = """
아래 카드뉴스 스크립트를 블로그 포스팅으로 확장해주세요.

주제: {topic}
후킹 문구: {hook}
해시태그: {hashtags}

슬라이드 내용:
{slides_text}
"""


# ── 마크다운 변환 ─────────────────────────────────────────

def script_to_markdown(script: CardNewsScript) -> str:
    """
    CardNewsScript를 SEO 친화적인 마크다운 블로그 포스트로 변환.
    GPT-4o-mini가 각 슬라이드를 섹션으로 확장하고 서론/결론을 추가.

    Returns:
        완성된 마크다운 문자열
    """
    slides_text = "\n".join(
        f"[슬라이드 {s.slide_number} / {s.slide_type}]\n"
        f"제목: {s.title}\n"
        f"본문: {s.body}"
        + (f"\n강조: {s.accent}" if s.accent else "")
        for s in script.slides
    )
    hashtag_str = " ".join(script.hashtags[:15])

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_BlogPost)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _BLOG_SYSTEM),
        ("human", _BLOG_HUMAN),
    ])
    chain = prompt | structured
    post: _BlogPost = chain.invoke({
        "topic": script.topic,
        "hook": script.hook,
        "hashtags": hashtag_str,
        "slides_text": slides_text,
    })

    # 마크다운 조립
    tag_line = " ".join(f"`{t}`" for t in script.hashtags[:15])
    lines = [
        f"# {post.title}",
        "",
        f"> {post.meta_description}",
        "",
        "---",
        "",
        post.intro,
        "",
    ]
    for section in post.sections:
        lines.append(section)
        lines.append("")

    lines += [
        "---",
        "",
        post.conclusion,
        "",
        "---",
        "",
        f"**태그:** {tag_line}",
        "",
        f"*이 글은 카드뉴스 '{script.topic}'를 블로그 포스팅으로 재구성한 콘텐츠입니다.*",
    ]

    return "\n".join(lines)


# ── 마크다운 → HTML 변환 (Tistory 발행용) ────────────────

def _md_to_html(md: str) -> str:
    """
    간단한 마크다운 → HTML 변환.
    (markdown 라이브러리 없는 환경을 위해 직접 구현)
    """
    try:
        import markdown as md_lib  # noqa: PLC0415
        return md_lib.markdown(md, extensions=["extra", "nl2br"])
    except ImportError:
        pass

    # 폴백: 직접 변환
    html_lines = []
    in_list = False

    for line in md.splitlines():
        # 수평선
        if re.match(r"^---+$", line.strip()):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<hr>")
            continue

        # 제목
        h_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h_match:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(h_match.group(1))
            text = _inline_md(h_match.group(2))
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue

        # 인용
        if line.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = _inline_md(line[2:])
            html_lines.append(f"<blockquote><p>{text}</p></blockquote>")
            continue

        # 목록
        if re.match(r"^[-*]\s+", line):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = _inline_md(re.sub(r"^[-*]\s+", "", line))
            html_lines.append(f"  <li>{text}</li>")
            continue

        # 목록 종료
        if in_list and line.strip() == "":
            html_lines.append("</ul>")
            in_list = False
            html_lines.append("<br>")
            continue

        # 빈 줄
        if line.strip() == "":
            html_lines.append("<br>")
            continue

        # 일반 문단
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        html_lines.append(f"<p>{_inline_md(line)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """인라인 마크다운 변환 (굵은 글씨, 이탤릭, 코드, 링크)"""
    # **굵은 글씨**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # *이탤릭*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # `코드`
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # [링크](url)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text


# ── Tistory 발행 ──────────────────────────────────────────

def publish_tistory(
    script: CardNewsScript,
    image_paths: list[Path],
    blog_name: str = "",
) -> str:
    """
    Tistory API v1로 블로그 포스팅 발행.

    Args:
        script:      CardNewsScript
        image_paths: 카드뉴스 이미지 경로 목록 (현재는 참조용, 썸네일 등에 확장 가능)
        blog_name:   Tistory 블로그 이름 (예: myblog)
                     비어있으면 .env TISTORY_BLOG_NAME 사용

    Returns:
        게시된 포스트 URL
    """
    import httpx  # noqa: PLC0415

    _blog = blog_name or TISTORY_BLOG_NAME
    if not _blog:
        raise EnvironmentError(
            ".env에 TISTORY_BLOG_NAME이 없습니다. (예: myblog → myblog.tistory.com)"
        )

    md_content = script_to_markdown(script)
    html_content = _md_to_html(md_content)

    # 태그: 해시태그에서 # 제거
    tags = ",".join(t.lstrip("#") for t in script.hashtags[:10])

    resp = httpx.post(
        f"{TISTORY_API_BASE}/post/write",
        params={
            "access_token": TISTORY_ACCESS_TOKEN,
            "output":       "json",
            "blogName":     _blog,
            "title":        script.topic,
            "content":      html_content,
            "visibility":   "3",   # 3 = 공개
            "tag":          tags,
        },
        timeout=30,
    )

    data = resp.json()
    tistory_data = data.get("tistory", {})
    status = tistory_data.get("status", "")

    if status != "200":
        raise RuntimeError(f"Tistory 발행 실패: {data}")

    post_url = tistory_data.get("url", f"https://{_blog}.tistory.com")
    return post_url


# ── 파일 저장 (폴백) ──────────────────────────────────────

def _save_as_file(script: CardNewsScript, image_paths: list[Path]) -> str:
    """
    API 키 없을 때 마크다운 파일로 저장.

    저장 경로: output/{날짜}_{topic}/blog_post.md
    Returns:
        저장된 파일의 절대 경로 문자열
    """
    md_content = script_to_markdown(script)

    # 저장 디렉터리 결정: 이미지가 있으면 같은 폴더, 없으면 output/{날짜}_{topic}
    if image_paths:
        output_dir = image_paths[0].parent
    else:
        safe_topic = re.sub(r"[^\w가-힣]", "_", script.topic)[:25]
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_dir = OUTPUT_DIR / f"{date_str}_{safe_topic}"
        output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / "blog_post.md"
    file_path.write_text(md_content, encoding="utf-8")
    return str(file_path.resolve())


# ── 공개 인터페이스 ───────────────────────────────────────

def publish(
    script: CardNewsScript,
    image_paths: list[Path],
) -> dict[str, str]:
    """
    블로그 포스팅 발행. TISTORY_ACCESS_TOKEN 유무에 따라 플랫폼 자동 선택.

    Args:
        script:      CardNewsScript
        image_paths: 카드뉴스 이미지 경로 목록

    Returns:
        {"platform": "tistory" | "file", "url": "..."}
    """
    if TISTORY_ACCESS_TOKEN:
        print("  [BlogPublisher] Tistory 발행 중...")
        try:
            url = publish_tistory(script, image_paths)
            print(f"  [BlogPublisher] Tistory 발행 완료: {url}")
            return {"platform": "tistory", "url": url}
        except Exception as e:
            print(f"  [BlogPublisher] Tistory 발행 실패: {e}")
            print("  [BlogPublisher] 마크다운 파일로 저장 전환...")

    print("  [BlogPublisher] 마크다운 파일로 저장 중...")
    file_path = _save_as_file(script, image_paths)
    print(f"  [BlogPublisher] 저장 완료: {file_path}")
    return {"platform": "file", "url": file_path}
