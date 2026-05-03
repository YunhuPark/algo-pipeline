"""
비디오 랜더러 모듈
YouTube 다운로드 영상과 카드뉴스 PNG 이미지를 합성하여 MP4 슬라이드를 만듭니다.
"""
from __future__ import annotations
from pathlib import Path

def create_video_slide(bg_image_path: Path, video_snippet_path: Path, output_path: Path, thumb_ratio: float = 0.45):
    """
    moviepy를 사용해 배경 이미지(PNG)와 유튜브 클립 영상(MP4)을 합성.
    영상은 카드 상단(0부터 thumb_ratio 비율까지)에 오버레이 됩니다.
    """
    from moviepy import ImageClip, VideoFileClip, CompositeVideoClip

    try:
        # 배경 이미지 (디자인 렌더러가 만들어둔 카드 이미지)
        bg_clip = ImageClip(str(bg_image_path))

        # 다운받은 유튜브 영상
        video_clip = VideoFileClip(str(video_snippet_path))

        # 영상 목표 사이즈 (1080 x 607 등)
        target_w = bg_clip.size[0]
        target_h = int(bg_clip.size[1] * thumb_ratio)

        # 영상을 꽉 차게 리사이즈 후 크롭 (원본 비율 유지)
        vid_w, vid_h = video_clip.size
        if (vid_w / vid_h) > (target_w / target_h):
            # 영상이 목표보다 더 넓음 -> 높이를 맞추고 좌우를 자름
            video_clip = video_clip.resized(height=target_h)
            new_w = video_clip.size[0]
            video_clip = video_clip.cropped(x_center=new_w/2, width=target_w)
        else:
            # 영상이 목표보다 좁거나 같음 -> 너비를 맞추고 위아래 자름
            video_clip = video_clip.resized(width=target_w)
            new_h = video_clip.size[1]
            video_clip = video_clip.cropped(y_center=new_h/2, height=target_h)

        # 목표 위치(상단)에 영상 배치
        video_clip = video_clip.with_position(("center", "top"))

        # 배경 길이를 영상 길이에 맞춤
        bg_clip = bg_clip.with_duration(video_clip.duration)

        # 영상 합성 (배경 위에 영상 얹기)
        final_clip = CompositeVideoClip([bg_clip, video_clip])

        # 오디오 유지
        final_clip.audio = video_clip.audio

        print(f"  [VideoRenderer] 동영상 슬라이드 렌더링 중: {output_path.name}")
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",   # 로컬 생성 속도 향상
            logger=None           # 불필요한 로그 숨김
        )

        bg_clip.close()
        video_clip.close()
        final_clip.close()

        return output_path
    except Exception as e:
        print(f"  [VideoRenderer] 오류 발생: {e}")
        return None


def render_reels(
    card_paths: list[Path],
    slide_types: list[str] | None = None,
    video_infos=None,
    slide_duration: float = 3.5,
    output_name: str = "reels.mp4",
    fade_duration: float = 0.3,    # 슬라이드 간 페이드 전환 (초)
    reels_aspect: str = "4:5",     # "4:5"=1080×1350 / "9:16"=1080×1920
) -> Path | None:
    """
    카드 PNG/MP4 파일들을 이어붙여 단일 릴스 MP4 생성.
    - MP4 슬라이드: 그대로 사용 (최대 15초), 앞뒤 페이드 적용
    - PNG 슬라이드: slide_duration초 정지 영상으로 변환, 앞뒤 페이드 적용
    - reels_aspect="9:16" 시 위아래에 검정 패딩 추가 (1080×1920)
    - 전체를 연결해 output_dir/reels.mp4 저장
    """
    try:
        from moviepy import ImageClip, VideoFileClip, concatenate_videoclips, ColorClip, CompositeVideoClip
        from moviepy import vfx
    except ImportError:
        print("  [VideoRenderer] moviepy 없음 — pip install moviepy")
        return None

    if not card_paths:
        return None

    out_dir = card_paths[0].parent
    out_path = out_dir / output_name

    # 출력 해상도 결정
    if reels_aspect == "9:16":
        out_w, out_h = 1080, 1920
    else:
        out_w, out_h = 1080, 1350

    clips = []
    for i, p in enumerate(card_paths):
        try:
            if p.suffix.lower() == ".mp4":
                clip = VideoFileClip(str(p))
                # 최대 15초 제한
                if clip.duration > 15:
                    clip = clip.subclipped(0, 15)
            else:
                clip = ImageClip(str(p)).with_duration(slide_duration)

            # 카드 크기를 출력 해상도에 맞게 조정
            if clip.size != (out_w, out_h):
                if reels_aspect == "9:16" and clip.size[1] == 1350:
                    # 1080×1350 카드를 1080×1920 캔버스 중앙에 배치 (위아래 검정 패딩)
                    pad_h = (out_h - 1350) // 2
                    bg = ColorClip(size=(out_w, out_h), color=(0, 0, 0), duration=clip.duration)
                    clip = CompositeVideoClip([
                        bg,
                        clip.with_position(("center", pad_h)),
                    ])
                else:
                    clip = clip.resized((out_w, out_h))

            # 페이드 인/아웃 적용 (moviepy 2.x API)
            if fade_duration > 0 and clip.duration > fade_duration * 2:
                clip = clip.with_effects([vfx.FadeIn(fade_duration), vfx.FadeOut(fade_duration)])

            clips.append(clip)
        except Exception as e:
            print(f"  [VideoRenderer] 슬라이드 {i+1} 로드 실패: {e}")

    if not clips:
        return None

    print(f"  [VideoRenderer] 릴스 합성 중 ({len(clips)}개 클립, {out_w}×{out_h})...")
    try:
        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(
            str(out_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            logger=None,
        )
        for c in clips:
            c.close()
        final.close()
        print(f"  [VideoRenderer] 릴스 저장: {out_path.name} ({out_path.stat().st_size/1024/1024:.1f}MB)")
        return out_path
    except Exception as e:
        print(f"  [VideoRenderer] 릴스 합성 실패: {e}")
        return None
