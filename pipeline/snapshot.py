"""Undo용 스냅샷 저장/복원."""

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path("data/snapshots")


class SnapshotManager:
    """파이프라인 상태 스냅샷을 관리하여 Undo를 지원한다."""

    def __init__(self, base_dir: Path = SNAPSHOT_DIR, max_snapshots: int = 50) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_snapshots = max_snapshots
        self._counter = 0

    def save(self, state: dict[str, Any]) -> Path:
        """현재 상태를 스냅샷으로 저장한다."""
        self._counter += 1
        timestamp = int(time.time() * 1000)
        filename = f"snapshot_{timestamp}_{self._counter:04d}.json"
        path = self.base_dir / filename

        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        logger.info("Snapshot saved: %s", path)
        self._prune()
        return path

    def restore_latest(self) -> dict[str, Any] | None:
        """가장 최근 스냅샷을 복원한다."""
        snapshots = self._list_snapshots()
        if not snapshots:
            logger.warning("No snapshots available")
            return None

        latest = snapshots[-1]
        with open(latest, encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Restored snapshot: %s", latest)
        return data

    def restore_by_index(self, index: int) -> dict[str, Any] | None:
        """인덱스로 특정 스냅샷을 복원한다 (0 = 가장 오래된 것)."""
        snapshots = self._list_snapshots()
        if 0 <= index < len(snapshots):
            with open(snapshots[index], encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_snapshots(self) -> list[str]:
        """저장된 스냅샷 목록을 반환한다."""
        return [str(p) for p in self._list_snapshots()]

    def _list_snapshots(self) -> list[Path]:
        return sorted(self.base_dir.glob("snapshot_*.json"))

    def save_files(
        self, project_id: str, step_id: str, files: list[str]
    ) -> str:
        """지정된 파일들의 스냅샷을 저장한다 (Undo 지원).

        Args:
            project_id: 프로젝트 식별자
            step_id: 단계 식별자
            files: 복사할 파일 경로 목록

        Returns:
            스냅샷 식별자
        """
        import shutil

        snap_id = f"{project_id}_{step_id}_{int(time.time() * 1000)}"
        snap_dir = self.base_dir / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        for f in files:
            src = Path(f)
            if src.exists():
                shutil.copy2(src, snap_dir / src.name)

        logger.info("File snapshot saved: %s (%d files)", snap_id, len(files))
        return snap_id

    def restore_files(self, snap_id: str, target_dir: str) -> list[str]:
        """스냅샷에서 파일을 복원한다.

        Returns:
            복원된 파일 경로 목록
        """
        import shutil

        snap_dir = self.base_dir / snap_id
        if not snap_dir.exists():
            raise FileNotFoundError(f"스냅샷 없음: {snap_id}")

        restored: list[str] = []
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        for f in snap_dir.iterdir():
            if f.is_file() and not f.name.endswith(".json"):
                dest = target / f.name
                shutil.copy2(f, dest)
                restored.append(str(dest))

        logger.info("Files restored from %s: %d files", snap_id, len(restored))
        return restored

    def list_project_snapshots(self, project_id: str) -> list[str]:
        """프로젝트의 스냅샷 목록을 반환한다."""
        return sorted([
            d.name for d in self.base_dir.iterdir()
            if d.is_dir() and d.name.startswith(project_id)
        ])

    def _prune(self) -> None:
        """max_snapshots를 초과하면 오래된 것부터 삭제한다."""
        snapshots = self._list_snapshots()
        while len(snapshots) > self.max_snapshots:
            oldest = snapshots.pop(0)
            oldest.unlink()
            logger.info("Pruned old snapshot: %s", oldest)
