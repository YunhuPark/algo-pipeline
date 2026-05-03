"""
Approval — 최종 결과물 확인 후 업로드 승인
────────────────────────────────────────────────────────
렌더링된 카드뉴스 이미지를 사용자가 직접 확인하고
업로드 여부를 결정한다.

응답 옵션:
  y (yes)   — 지금 바로 Instagram 업로드
  n (no)    — 업로드 취소 (이미지는 output/ 에 보관)
  r (retry) — 카드뉴스 재생성 (pipeline 재시작)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _open_folder(folder: Path) -> None:
    """OS에 맞게 파일 탐색기로 폴더 열기."""
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)
    except Exception as e:
        print(f"  폴더 자동 열기 실패: {e}")
        print(f"  직접 열어주세요: {folder}")


def _print_card_list(image_paths: list[Path]) -> None:
    pngs = [p for p in image_paths if p.suffix.lower() == ".png"]
    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print("  │  생성된 카드뉴스 이미지                         │")
    print("  ├─────────────────────────────────────────────────┤")
    for p in pngs:
        print(f"  │  📄 {p.name}")
    print("  ├─────────────────────────────────────────────────┤")
    print(f"  │  📁 폴더: {pngs[0].parent}")
    print("  └─────────────────────────────────────────────────┘")
    print()


def wait_for_approval(
    image_paths: list[Path],
    auto: bool = False,
) -> str:
    """
    이미지 폴더를 열고 사용자 승인을 기다린다.

    Args:
        image_paths: 렌더링된 이미지 파일 목록
        auto:        True면 사용자 입력 없이 자동 승인 (에이전트 모드)

    Returns:
        "upload"  — 업로드 진행
        "skip"    — 업로드 취소
        "retry"   — 재생성 요청
    """
    pngs = [p for p in image_paths if p.suffix.lower() == ".png"]
    if not pngs:
        return "upload"

    folder = pngs[0].parent

    if auto:
        print(f"  [Approval] 자동 승인 (에이전트 모드) → {folder.name}")
        return "upload"

    # 폴더 열기 + 목록 출력
    _open_folder(folder)
    _print_card_list(image_paths)

    print("  이미지를 확인하고 다음 중 선택하세요:")
    print("    [y] 업로드  [n] 취소  [r] 재생성")
    print()

    while True:
        try:
            ans = input("  선택 (y/n/r): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  취소됨.")
            return "skip"

        if ans in ("y", "yes", "ㅛ"):
            print("  ✓ 업로드 승인\n")
            return "upload"
        elif ans in ("n", "no", "ㅜ"):
            print(f"  ✓ 업로드 취소. 이미지는 보관됩니다: {folder}\n")
            return "skip"
        elif ans in ("r", "retry", "ㄱ"):
            print("  ✓ 재생성 요청\n")
            return "retry"
        else:
            print("  y, n, r 중 하나를 입력하세요.")
