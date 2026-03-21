"""Fusion 프로세스 상태 감시 및 자동 재시작.

Fusion 360이 크래시하거나 응답하지 않을 때:
1. 프로세스 종료를 감지
2. 자동 재시작
3. 마지막 체크포인트에서 작업 재개

별도 스레드 또는 프로세스로 실행한다.
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# Fusion 360 프로세스 이름 (Windows)
FUSION_PROCESS_NAME = "Fusion360.exe"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18080"


@dataclass
class MonitorConfig:
    bridge_url: str = DEFAULT_BRIDGE_URL
    check_interval: float = 10.0  # 초
    heartbeat_timeout: float = 30.0  # heartbeat 없으면 비정상
    max_restart_attempts: int = 3
    restart_cooldown: float = 30.0  # 재시작 후 대기
    fusion_exe_path: str | None = None  # 없으면 자동 재시작 불가


@dataclass
class MonitorState:
    is_running: bool = False
    last_check: float = 0.0
    last_healthy: float = 0.0
    restart_count: int = 0
    restart_timestamps: list[float] = field(default_factory=list)


class HealthMonitor:
    """Fusion 360 프로세스를 감시하고 필요 시 복구한다."""

    def __init__(
        self,
        config: MonitorConfig | None = None,
        on_crash: Callable[[], None] | None = None,
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        self.config = config or MonitorConfig()
        self.state = MonitorState()
        self._on_crash = on_crash
        self._on_restart = on_restart
        self._should_stop = False

    def check_once(self) -> bool:
        """한 번 상태를 확인하고 결과를 반환한다."""
        self.state.last_check = time.time()

        # 1. 브릿지 서버 health 확인
        bridge_healthy = self._check_bridge()

        # 2. 프로세스 존재 확인
        process_running = self._check_process()

        healthy = bridge_healthy and process_running
        if healthy:
            self.state.last_healthy = time.time()
            self.state.is_running = True

        return healthy

    def run_monitor_loop(self) -> None:
        """모니터링 루프를 실행한다 (blocking)."""
        logger.info("Health monitor started (interval=%.1fs)", self.config.check_interval)
        self._should_stop = False

        while not self._should_stop:
            healthy = self.check_once()

            if not healthy:
                logger.warning("Fusion 360 unhealthy")
                self.state.is_running = False

                if self._on_crash:
                    self._on_crash()

                if self._can_restart():
                    self._attempt_restart()

            time.sleep(self.config.check_interval)

    def stop(self) -> None:
        """모니터링을 중지한다."""
        self._should_stop = True

    def _check_bridge(self) -> bool:
        """브릿지 서버 health 엔드포인트를 확인한다."""
        try:
            resp = httpx.get(f"{self.config.bridge_url}/health", timeout=5)
            data = resp.json()
            return data.get("fusion_running", False)
        except Exception:
            return False

    def _check_process(self) -> bool:
        """Fusion 360 프로세스가 실행 중인지 확인한다."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {FUSION_PROCESS_NAME}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return FUSION_PROCESS_NAME.lower() in result.stdout.lower()
        except Exception:
            return False

    def _can_restart(self) -> bool:
        """재시작이 가능한지 판단한다."""
        if self.config.fusion_exe_path is None:
            return False

        if self.state.restart_count >= self.config.max_restart_attempts:
            logger.error(
                "Max restart attempts (%d) reached", self.config.max_restart_attempts
            )
            return False

        # 쿨다운 확인
        if self.state.restart_timestamps:
            last_restart = self.state.restart_timestamps[-1]
            if time.time() - last_restart < self.config.restart_cooldown:
                return False

        return True

    def _attempt_restart(self) -> None:
        """Fusion 360 재시작을 시도한다."""
        if not self.config.fusion_exe_path:
            return

        exe_path = Path(self.config.fusion_exe_path)
        if not exe_path.exists():
            logger.error("Fusion executable not found: %s", exe_path)
            return

        logger.info("Attempting Fusion 360 restart (#%d)", self.state.restart_count + 1)

        try:
            subprocess.Popen([str(exe_path)], start_new_session=True)
            self.state.restart_count += 1
            self.state.restart_timestamps.append(time.time())

            # 재시작 후 대기
            logger.info("Waiting %.0fs for Fusion to initialize...", self.config.restart_cooldown)
            time.sleep(self.config.restart_cooldown)

            if self._on_restart:
                self._on_restart()

        except Exception as e:
            logger.error("Failed to restart Fusion: %s", e)

    def get_status(self) -> dict:
        """현재 모니터 상태를 반환한다."""
        return {
            "is_running": self.state.is_running,
            "last_check": self.state.last_check,
            "last_healthy": self.state.last_healthy,
            "restart_count": self.state.restart_count,
            "can_restart": self._can_restart(),
        }
