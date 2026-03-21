"""Fusion 360 어댑터 — Fusion 내장 HTTP 서버를 통해 통신한다.

아키텍처:
    오케스트레이터 → Fusion360Adapter → Fusion Add-in (HTTP :18080) → Fusion 360 엔진

Fusion 360 애드인이 직접 HTTP 서버를 내장하고 있으므로
별도의 브릿지 프로세스 없이 애드인에 직접 HTTP 요청을 보낸다.
"""

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from adapters.base import BaseAdapter, Status, ToolResult, ValidationCheck

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent / "tool_registry.json"


class Fusion360Adapter(BaseAdapter):
    """Fusion 360 어댑터. 로컬 브릿지 서버를 통해 Fusion과 통신한다."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._bridge_url = self.config.get("bridge_url", "http://127.0.0.1:18080")
        self._timeout = self.config.get("timeout", 60)
        self._registry: dict[str, dict] | None = None
        # 로컬 HTTP 전용 — SSL 검증 불필요, SSL_CERT_FILE 경로 문제 회피
        self._http_client = httpx.Client(timeout=self._timeout, verify=False)

    # ─── BaseAdapter 인터페이스 구현 ───

    def execute(self, action: str, parameters: dict, context: dict) -> ToolResult:
        """브릿지 서버에 명령을 전송하고 결과를 받는다."""
        caps = self.get_capabilities()
        if action not in caps:
            return self._make_result(
                Status.FAILURE, {}, error=f"Unknown action: {action}"
            )

        # 스냅샷 요청 (context에 project_id가 있으면)
        if context.get("snapshot_before", False):
            self._save_snapshot(
                context.get("project_id", "default"),
                context.get("step_id", "unknown"),
            )

        try:
            response = self._http_client.post(
                f"{self._bridge_url}/execute",
                json={
                    "action": action,
                    "parameters": parameters,
                    "context": context,
                },
            )
            response.raise_for_status()
            data = response.json()

            return ToolResult(
                tool="fusion360",
                status=Status(data["status"]),
                result=data.get("result", {}),
                validation=self._parse_validation(data.get("validation", [])),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            return self._make_result(
                Status.FAILURE,
                {},
                error="Fusion 360 응답 타임아웃. Fusion이 실행 중인지 확인 필요",
            )
        except httpx.ConnectError:
            return self._make_result(
                Status.FAILURE,
                {},
                error="브릿지 서버 연결 실패. Fusion 애드인이 활성화되어 있는지 확인 필요",
            )
        except httpx.HTTPStatusError as e:
            return self._make_result(
                Status.FAILURE,
                {},
                error=f"브릿지 서버 HTTP 오류: {e.response.status_code}",
            )
        except Exception as e:
            logger.error("Fusion360 execute failed: %s", e, exc_info=True)
            return self._make_result(Status.FAILURE, {}, error=str(e))

    def validate_result(self, result: ToolResult) -> ToolResult:
        """기본 검증 + 부피/바운딩박스 정합성 체크."""
        if result.status == Status.FAILURE:
            return result

        # 부피가 있으면 양수인지 체크
        volume = result.result.get("volume_mm3")
        if volume is not None and volume <= 0:
            result.validation.append(
                ValidationCheck(
                    name="volume_positive",
                    status=Status.FAILURE,
                    value=volume,
                    threshold="> 0",
                    message="생성된 바디의 부피가 0 이하",
                )
            )
            result.status = Status.FAILURE

        # 바운딩박스 체크 (모든 차원 양수)
        bbox = result.result.get("bounding_box")
        if bbox and isinstance(bbox, dict):
            for dim in ("x", "y", "z"):
                size = bbox.get(f"{dim}_size_mm", 0)
                if size is not None and size < 0:
                    result.validation.append(
                        ValidationCheck(
                            name=f"bbox_{dim}_positive",
                            status=Status.FAILURE,
                            value=size,
                            message=f"바운딩박스 {dim} 크기가 음수: {size}",
                        )
                    )

        return result

    def get_capabilities(self) -> dict:
        """tool_registry.json에서 로드하여 반환한다."""
        if self._registry is None:
            self._registry = self._load_registry()
        return self._registry

    def health_check(self) -> bool:
        """브릿지 서버와 Fusion 프로세스 상태를 확인한다."""
        try:
            resp = self._http_client.get(f"{self._bridge_url}/health", timeout=5)
            data = resp.json()
            return data.get("fusion_running", False)
        except Exception:
            return False

    # ─── 내부 메서드 ───

    def _load_registry(self) -> dict[str, dict]:
        """tool_registry.json에서 도구 정의를 로드한다."""
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            registry = json.load(f)

        return {
            tool["name"]: {
                "description": tool["description"],
                "parameters_schema": tool["parameters_schema"],
            }
            for tool in registry["tools"]
        }

    def _save_snapshot(self, project_id: str, step_id: str) -> None:
        """Fusion 파일의 현재 상태를 저장한다 (Undo 지원)."""
        try:
            self._http_client.post(
                f"{self._bridge_url}/snapshot",
                json={"project_id": project_id, "step_id": step_id},
                timeout=10,
            )
        except Exception as e:
            self._logger.warning("Snapshot save failed (non-fatal): %s", e)

    def _parse_validation(self, checks: list[dict]) -> list[ValidationCheck]:
        """브릿지 서버 응답의 validation 배열을 ValidationCheck 리스트로 변환한다."""
        result = []
        for c in checks:
            try:
                result.append(
                    ValidationCheck(
                        name=c["name"],
                        status=Status(c["status"]),
                        value=c.get("value"),
                        threshold=c.get("threshold"),
                        message=c.get("message", ""),
                    )
                )
            except (KeyError, ValueError) as e:
                self._logger.warning("Invalid validation check: %s (%s)", c, e)
        return result
