"""FastAPI 브릿지 서버 — 오케스트레이터와 Fusion 360 애드인 사이의 통신 중계.

Fusion 360 API는 메인 UI 스레드에서만 동작하므로, 외부에서
HTTP 요청을 받아 Fusion 애드인으로 전달하고 결과를 반환한다.

통신 흐름:
    1. /execute 엔드포인트로 명령 수신
    2. 명령을 request 파일로 저장 (파일 기반 통신)
    3. Fusion 애드인이 request 파일을 감지하고 실행
    4. 애드인이 response 파일을 작성
    5. 브릿지 서버가 response를 읽어 HTTP 응답으로 반환

사용법:
    python -m adapters.fusion360.bridge_server
    또는
    uvicorn adapters.fusion360.bridge_server:app --host 127.0.0.1 --port 18080
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 애드인과의 파일 통신 디렉토리
COMM_DIR = Path(__file__).parent / "addin" / ".comm"
COMM_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Fusion 360 Bridge Server", version="0.1.0")


# ─── Request/Response 모델 ───


class ExecuteRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}
    context: dict[str, Any] = {}


class ExecuteResponse(BaseModel):
    status: str  # "success" | "failure" | "warning"
    result: dict[str, Any] = {}
    validation: list[dict[str, Any]] = []
    error: str | None = None


class SnapshotRequest(BaseModel):
    project_id: str
    step_id: str


class HealthResponse(BaseModel):
    fusion_running: bool
    addin_connected: bool
    last_heartbeat: float | None = None
    pending_requests: int = 0


# ─── 상태 관리 ───


class BridgeState:
    """브릿지 서버의 내부 상태."""

    def __init__(self) -> None:
        self.last_heartbeat: float | None = None
        self._pending: dict[str, Path] = {}  # request_id → request file

    @property
    def addin_connected(self) -> bool:
        """애드인이 최근 30초 내에 heartbeat를 보냈는지."""
        if self.last_heartbeat is None:
            return False
        return (time.time() - self.last_heartbeat) < 30.0

    @property
    def fusion_running(self) -> bool:
        """Fusion 프로세스가 실행 중인지 (heartbeat 기반)."""
        return self.addin_connected


_state = BridgeState()

# ─── 파일 기반 통신 ───

POLL_INTERVAL = 0.2  # 초
MAX_WAIT = 120.0  # 최대 대기 시간


def _write_request(request_id: str, action: str, parameters: dict, context: dict) -> Path:
    """요청 파일을 작성한다. 애드인이 이 파일을 감지하여 실행한다."""
    request_file = COMM_DIR / f"req_{request_id}.json"
    payload = {
        "request_id": request_id,
        "action": action,
        "parameters": parameters,
        "context": context,
        "timestamp": time.time(),
    }
    request_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return request_file


def _wait_for_response(request_id: str, timeout: float = MAX_WAIT) -> dict | None:
    """응답 파일이 생성될 때까지 폴링한다."""
    response_file = COMM_DIR / f"resp_{request_id}.json"
    start = time.time()

    while (time.time() - start) < timeout:
        if response_file.exists():
            try:
                data = json.loads(response_file.read_text(encoding="utf-8"))
                # 처리 완료 후 파일 정리
                response_file.unlink(missing_ok=True)
                req_file = COMM_DIR / f"req_{request_id}.json"
                req_file.unlink(missing_ok=True)
                return data
            except json.JSONDecodeError:
                # 파일 쓰기 중일 수 있음, 다시 대기
                pass
        time.sleep(POLL_INTERVAL)

    # 타임아웃: 요청 파일도 정리
    req_file = COMM_DIR / f"req_{request_id}.json"
    req_file.unlink(missing_ok=True)
    return None


# ─── API 엔드포인트 ───


@app.post("/execute", response_model=ExecuteResponse)
async def execute_command(request: ExecuteRequest) -> ExecuteResponse:
    """Fusion 360에서 명령을 실행한다."""
    if not _state.addin_connected:
        raise HTTPException(
            status_code=503,
            detail="Fusion 360 애드인이 연결되지 않았습니다. Fusion을 시작하고 애드인을 활성화하세요.",
        )

    request_id = uuid.uuid4().hex[:12]
    logger.info("Execute request %s: %s", request_id, request.action)

    _write_request(request_id, request.action, request.parameters, request.context)
    _state._pending[request_id] = COMM_DIR / f"req_{request_id}.json"

    response_data = _wait_for_response(request_id)
    _state._pending.pop(request_id, None)

    if response_data is None:
        return ExecuteResponse(
            status="failure",
            error=f"Fusion 360 응답 타임아웃 ({MAX_WAIT}초). 작업이 너무 오래 걸리거나 Fusion이 응답하지 않습니다.",
        )

    return ExecuteResponse(
        status=response_data.get("status", "failure"),
        result=response_data.get("result", {}),
        validation=response_data.get("validation", []),
        error=response_data.get("error"),
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """브릿지 서버 및 Fusion 360 상태를 반환한다."""
    return HealthResponse(
        fusion_running=_state.fusion_running,
        addin_connected=_state.addin_connected,
        last_heartbeat=_state.last_heartbeat,
        pending_requests=len(_state._pending),
    )


@app.post("/heartbeat")
async def receive_heartbeat() -> dict:
    """Fusion 애드인이 주기적으로 보내는 heartbeat를 수신한다."""
    _state.last_heartbeat = time.time()
    return {"acknowledged": True}


@app.post("/snapshot")
async def save_snapshot(request: SnapshotRequest) -> dict:
    """현재 설계 상태의 스냅샷을 요청한다."""
    request_id = uuid.uuid4().hex[:12]
    _write_request(
        request_id,
        "_internal_snapshot",
        {"project_id": request.project_id, "step_id": request.step_id},
        {},
    )
    response_data = _wait_for_response(request_id, timeout=15.0)
    if response_data is None:
        return {"status": "warning", "message": "스냅샷 타임아웃, 비치명적"}
    return {"status": "success"}


@app.get("/pending")
async def list_pending() -> dict:
    """대기 중인 요청 목록을 반환한다."""
    return {
        "count": len(_state._pending),
        "request_ids": list(_state._pending.keys()),
    }


# ─── 서버 실행 ───


def run_server(host: str = "127.0.0.1", port: int = 18080) -> None:
    """브릿지 서버를 실행한다."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
