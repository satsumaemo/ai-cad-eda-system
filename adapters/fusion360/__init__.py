"""Fusion 360 어댑터 패키지.

아키텍처:
    adapter.py          — BaseAdapter 구현. 오케스트레이터와의 인터페이스
    bridge_server.py    — FastAPI 브릿지 서버. Fusion 애드인과 HTTP 통신
    tool_registry.json  — 지원하는 Fusion 작업 목록 정의
    script_generator.py — tool_use 파라미터 → Fusion Python 스크립트 생성
    health_monitor.py   — Fusion 프로세스 상태 감시, 자동 재시작
    addin/              — Fusion 360 내부에 설치하는 애드인
    scripts/            — 독립 실행 가능한 Fusion Python 스크립트 템플릿
"""

from adapters.fusion360.adapter import Fusion360Adapter

__all__ = ["Fusion360Adapter"]
