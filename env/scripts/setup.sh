#!/bin/bash
set -e

echo "=== CAD/EDA 통합 설계 시스템 초기 세팅 ==="

# 1. .env 생성
if [ ! -f .env ]; then
    cp env/.env.example .env
    echo "[OK] .env 생성됨. ANTHROPIC_API_KEY를 설정해주세요."
else
    echo "[..] .env 이미 존재"
fi

# 2. Python 가상환경
if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "[OK] .venv 생성됨"
fi
source .venv/bin/activate

# 3. 의존성 설치
pip install -r env/requirements/all.txt
echo "[OK] Python 의존성 설치 완료"

# 4. 디렉토리 생성
mkdir -p data/{snapshots,results,logs,work}
echo "[OK] 데이터 디렉토리 생성 완료"

# 5. Docker 이미지 빌드 (Docker 설치 시)
if command -v docker &> /dev/null; then
    echo "Docker 이미지 빌드 중..."
    cd env/docker
    docker compose build || echo "[..] Docker 빌드 실패 (비필수)"
    cd ../..
    echo "[OK] Docker 이미지 빌드 완료"
else
    echo "[..] Docker 미설치, 시뮬레이션 도구를 로컬에 설치하세요"
fi

# 6. 도구 상태 확인
python env/scripts/check_tools.py

echo ""
echo "=== 세팅 완료 ==="
echo "다음 단계:"
echo "  1. .env에 ANTHROPIC_API_KEY 설정"
echo "  2. Fusion 360 실행 + 애드인 활성화 (Fusion 사용 시)"
echo "  3. python env/scripts/check_tools.py 로 상태 재확인"
