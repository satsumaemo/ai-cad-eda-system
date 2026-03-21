# AI 기반 CAD/EDA 통합 설계 시스템

## 빌드/테스트 명령
- 전체 테스트: `pytest`
- 특정 모듈: `pytest tests/test_adapters/test_fusion360.py`
- 커버리지: `pytest --cov=. --cov-report=term-missing`
- 설계 준수 검증: `pytest tests/test_design_compliance.py`
- 환경 확인: `python env/scripts/check_tools.py`

## 초기 세팅
```bash
cp env/.env.example .env           # .env 생성 후 API 키 설정
pip install -r env/requirements/all.txt  # 전체 의존성 설치
# 또는 모듈별:
pip install -r env/requirements/orchestrator.txt
pip install -r env/requirements/dev.txt
```

## 코딩 컨벤션
- Python 3.11+, 타입 힌트 필수
- 어댑터는 반드시 `adapters.base.BaseAdapter` 상속
- 설정값 하드코딩 금지 → `config/` YAML에서 로드
- 환경변수는 `.env`에서 `python-dotenv`로 로드. 코드에 API 키 등 직접 작성 금지
- 시뮬레이션 도구는 Docker 실행 우선, 로컬 실행 폴백 지원
- Python 의존성은 `env/requirements/`에 모듈별 분리 관리
- logging 모듈 사용, 구조화된 JSON 로그
- 각 모듈은 독립 테스트 가능해야 함
- 내부 단위: SI (m, kg, s), 어댑터가 입출력 시 `validation/unit_converter.py`로 변환
- 데이터 포맷: CAD→시뮬 STEP AP214, 메시 Gmsh, 결과 JSON/VTK

## 핵심 원칙
- 어댑터 패턴: 새 도구 추가 시 기존 코드 수정 금지
- 오케스트레이터는 도구 내부 구현을 모름. 도구 간 직접 import 금지
- "정보 수집 → 확인 → 실행": 사용자 승인 없이 설계 변경 금지
- 수치 데이터 기반 판단: 이미지 분석 의존 금지
- pass/fail 기준은 `config/pass_fail_criteria.yaml`에 외부화

## 디렉토리 구조 핵심
```
config/          설정 YAML + loader.py
orchestrator/    Claude API 메인 루프, system prompt, planner
adapters/        도구 어댑터 (base.py, simulation_base.py, 각 도구별)
pipeline/        실행 엔진, 상태, 스냅샷, 오차 버짓, 의존성 그래프
validation/      게이트, 형상 검증, 메시 검사, 단위 변환, 기준 로더
env/             .env, Docker, requirements, 스크립트
data/            작업/스냅샷/결과/로그
tests/           단위/통합/설계준수/회귀 테스트
```
