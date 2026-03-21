#!/bin/bash
set -e

echo "=== 서비스 시작 ==="

# .env 로드
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 1. Docker 시뮬레이션 도구 (필요시)
if command -v docker &> /dev/null; then
    echo "Docker 컨테이너 준비 확인..."
    cd env/docker
    docker compose up -d --no-recreate 2>/dev/null || echo "[..] Docker 서비스 없음 (비필수)"
    cd ../..
fi

# 2. Fusion 브릿지 서버 (백그라운드)
if [ -n "$FUSION_BRIDGE_URL" ]; then
    echo "Fusion 브릿지 서버 시작..."
    python -m adapters.fusion360.bridge_server &
    BRIDGE_PID=$!
    echo "  PID: $BRIDGE_PID"
fi

echo ""
echo "=== 서비스 시작 완료 ==="
echo "오케스트레이터 실행: python -m orchestrator.core"
