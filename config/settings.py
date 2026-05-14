from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 필수
    anthropic_api_key: str = ""

    # 기본값
    default_style: str = "bold_gradient"
    default_num_cards: int = 6
    output_base_dir: Path = Path("output")
    font_dir: Path = Path("styles/assets/fonts")

    # 렌더링
    image_width: int = 1080
    image_height: int = 1350
    image_quality: int = 95

    # 조사
    max_trends: int = 10
    max_news_articles: int = 15

    # 브랜드
    account_handle: str = "@cardnews_ai"
    watermark_enabled: bool = True


def get_settings() -> Settings:
    return Settings()
