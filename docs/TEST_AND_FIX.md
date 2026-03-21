# TEST_AND_FIX 절차

## 시나리오 실행 절차

1. **테스트 작성**: 시나리오별 독립 테스트 함수 작성
2. **실행**: `python tests/test_scenarios.py` 또는 개별 함수 실행
3. **실패 시 수정**: 최대 3회 시도
   - 1차: 파라미터 조정 (메시 크기, 솔버 설정)
   - 2차: 어댑터/입력 생성기 코드 수정
   - 3차: 인프라 수정 (Docker, 환경)
4. **기존 테스트 확인**: `pytest tests/ -x --timeout=120`으로 기존 테스트 깨짐 확인
5. **기록**: 각 수정의 변경 파일과 사유 기록

## 수정 규칙

- 어댑터 수정 시 `BaseAdapter`/`SimulationAdapter` 인터페이스 유지
- 설정값 하드코딩 금지 → `config/` YAML에서 로드
- Docker 실행 시 타임아웃 180초 제한
- 애드인 수정 시 "애드인 재시작 필요" 명시

## 검증 기준

- pass_fail_criteria.yaml의 기준 적용
- 수치 데이터 기반 판정 (이미지 분석 금지)
- 물리적으로 합리적인 결과인지 sanity check
