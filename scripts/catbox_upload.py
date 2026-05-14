"""catbox.moe에 이미지 업로드 후 Instagram 캐러셀 게시."""
import sys, json, os, time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests


def upload_to_catbox(image_path: Path) -> str:
    with open(image_path, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (image_path.name, f, "image/png")},
            timeout=60,
        )
    url = r.text.strip()
    if not url.startswith("https://"):
        raise ValueError(f"catbox 업로드 실패: {url}")
    return url


def main():
    # 원본 폴더 메타데이터
    orig_name = sys.argv[1] if len(sys.argv) > 1 else "20260509_1130_OpenAI_Codex의_안전한_운영"
    orig_folder = ROOT / "output" / orig_name
    script_data = json.loads((orig_folder / "script.json").read_text(encoding="utf-8"))
    hook = script_data.get("hook", "")
    hashtags = script_data.get("hashtags", [])

    # 이미지 경로 (_upload_temp)
    temp_folder = ROOT / "output" / "_upload_temp"
    image_paths = sorted(temp_folder.glob("card_*.png"))
    print(f"이미지 {len(image_paths)}장 catbox.moe 업로드 중...")

    catbox_urls = []
    for img_path in image_paths:
        print(f"  {img_path.name}...", end=" ", flush=True)
        url = upload_to_catbox(img_path)
        catbox_urls.append(url)
        print(url)
        time.sleep(0.5)

    print(f"\n{len(catbox_urls)}개 URL 준비 완료. Instagram 업로드 시작...")

    # Instagram API 직접 호출
    from src.agents.publisher import (
        _create_media_container, _wait_for_ready,
        _create_carousel_container, _publish_carousel,
        _build_caption, get_post_permalink,
        IG_ACCESS_TOKEN, IG_USER_ID,
    )

    container_ids = []
    for url in catbox_urls:
        print(f"  컨테이너 생성: {url.split('/')[-1]}")
        cid = _create_media_container(url, is_carousel_item=True, is_video=False)
        print(f"    → {cid}")
        _wait_for_ready(cid)
        container_ids.append(cid)

    caption = _build_caption(hook, hashtags)
    print("캐러셀 컨테이너 생성...")
    carousel_id = _create_carousel_container(container_ids, caption)
    _wait_for_ready(carousel_id)

    print("게시 중...")
    post_id = _publish_carousel(carousel_id)
    permalink = get_post_permalink(post_id)
    print(f"\n업로드 완료! → {permalink or post_id}")

    # DB 저장
    from src.db import insert_post
    from datetime import datetime
    insert_post(
        platform="instagram",
        topic=script_data.get("topic", ""),
        post_id=post_id,
        angle="",
        hook=hook,
        hashtags=hashtags,
        image_dir=orig_folder.name,
        posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    print("DB 저장 완료")

    # 사이트 자동 반영
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_meta.py")], check=False)


if __name__ == "__main__":
    main()
