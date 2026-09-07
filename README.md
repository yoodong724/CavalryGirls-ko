# 철기 소녀 한글패치

Steam Cavalry Girls 2.6.2859 / 빌드 24639430 / Windows x64용 비공식 한국어 패치입니다. 일본어 언어 슬롯을 한국어로 대체합니다.

Codex를 이용하여 제작된 패치이며, 검수가 완료되지 않은 상태임을 밝힙니다.

설치 파일은 이 저장소의 **Releases**에서 `CavalryGirls-Korean-2.6.2859-build24639430-r05-public1.zip`을 내려받으세요. GitHub의 자동 생성 `Source code (zip)`은 설치 파일이 아닙니다.

설치와 복원은 [설치 안내](release/installer/README.ko.md), 변경 사항과 확인 범위는 [r05 릴리스 노트](RELEASE-r05.md)를 참고하세요.

## 소스 구성

- `localization/`: 승인 번역 8,774개와 재삽입에 필요한 원문 대조·위치 데이터.
- `release/reference/ui-r05.json`: 추가 고정 UI 4개 검수본과 레이아웃 수정 레시피. 총 번역 대상은 8,778개입니다.
- `adapters/`, `tools/maintenance/`: 원본 검사, 텍스트·폰트·타이틀·UI 패치, 차분 패키징.
- `release/installer/`: Windows 설치·복원 도구, xdelta3 및 라이선스.
- `release/assets/`: 한국어 타이틀 이미지와 적용용 DXT5 자산.
- `schemas/`, `tools/l10n.py`, `tests/`: 빌드 시 필요한 검증 코드와 회귀 검사.

[빌드 안내](BUILD.md)의 절차로 별도 게임 사본에서 재빌드할 수 있습니다. 게임 실행 파일과 전체 자산, 개발 작업 이력은 포함하지 않습니다. 소스 공개 범위와 제삼자 자료는 [권리 안내](RIGHTS.md)를 참고하세요.
