"""FusionBridge 애드인 동기화 스크립트.

프로젝트의 adapters/fusion360/addin/ 전체를
%APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\FusionBridge\\로 복사한다.

사용법:
    python env/scripts/sync_addin.py
"""

import os
import shutil
import sys
from pathlib import Path

# 프로젝트 루트 (이 스크립트 기준 ../../)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_DIR = PROJECT_ROOT / "adapters" / "fusion360" / "addin"

# Fusion 360 애드인 경로
APPDATA = os.environ.get("APPDATA", "")
TARGET_DIR = Path(APPDATA) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns" / "FusionBridge"

# 복사 제외 패턴
EXCLUDE = {"__pycache__", ".pyc", ".comm"}


def sync() -> None:
    if not SOURCE_DIR.is_dir():
        print(f"[ERROR] Source directory not found: {SOURCE_DIR}")
        sys.exit(1)

    if not APPDATA:
        print("[ERROR] %APPDATA% environment variable not set")
        sys.exit(1)

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src_path in SOURCE_DIR.rglob("*"):
        # 제외 패턴 확인
        if any(ex in src_path.parts for ex in EXCLUDE):
            continue
        if src_path.suffix in EXCLUDE:
            continue

        rel = src_path.relative_to(SOURCE_DIR)
        dst_path = TARGET_DIR / rel

        if src_path.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            copied += 1

    print(f"[OK] {copied} files synced")
    print(f"  Source: {SOURCE_DIR}")
    print(f"  Target: {TARGET_DIR}")
    print()
    print("Fusion 360에서 애드인을 재시작하세요:")
    print("  UTILITIES > Scripts and Add-Ins > Add-Ins > FusionBridge > Stop > Run")


if __name__ == "__main__":
    sync()
