# r05 소스 빌드

Linux 또는 WSL, Python 3.11 이상, `requirements.txt`의 패키지와 PATH의 `xdelta3`가 필요합니다. 기준 환경은 UnityPy 1.25.3, Linux xdelta3 3.0.11입니다. Windows 설치용 xdelta3 3.2.0은 `release/installer/bin/`에 포함돼 있습니다.

먼저 가상 환경을 만들고 의존성을 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

사용자가 보유한 지원 버전의 깨끗한 게임 사본을 준비하세요. 아래 명령은 입력한 경로를 읽기 전용으로 취급하며, 결과는 이 저장소의 `work/`에만 생성합니다. 게임 파일을 이 저장소에 복사하거나 커밋하지 마세요. 원본·결과·차분 검증에 여러 GB의 공간이 필요합니다.

```bash
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --ui-fixes --ui-revision r05 --output work/build-r05
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --build work/build-r05 --output work/package-r05
```

출력 폴더가 이미 존재하면 중단합니다. 다시 만들 때는 새 출력 이름을 사용하세요. 원본 파일 해시가 다르면 지원 버전 사본을 준비해야 합니다. 해시 검사를 우회하지 마세요.

빌드는 `resources.assets`와 `resources.assets.resS`를 만들고, 패키징은 차분을 생성한 뒤 독립 복원 해시를 확인합니다. `work/package-r05` 내용을 `CavalryGirls-Korean-r05` 폴더 안에 넣어 ZIP으로 묶습니다. 설치기는 패치 폴더 바로 위를 게임 폴더로 사용합니다.

공개 ZIP의 `package-manifest.json`에 기록된 `targets[].built_sha256`와 재빌드의 `build-manifest.json` 출력 해시가 같아야 합니다. ZIP 압축·문서·경로 메타데이터 때문에 ZIP 자체의 해시는 재빌드마다 달라질 수 있습니다. 공개 ZIP public1은 사용자 확인 r05의 설치 코드와 차분을 그대로 유지하고 안내 문서와 패키지 메타데이터만 갱신했습니다.

원문·번역·레시피는 고정된 검수 revision을 유지합니다. 빌더의 의존성 해시는 입력 변경을 검출하므로 파일을 수정하면 중단될 수 있습니다. 변경된 번역은 새 검수와 영향 화면 확인이 필요합니다. 이 저장소는 r05 재현에 필요한 자료를 담으며 과거 번역 작업 도구의 모든 기능이나 전체 검수 이력을 제공하지 않습니다.

실제 게임 실행·화면·세이브 검사는 [수동 확인표](release/installer/MANUAL-TEST.ko.md)를 사용합니다. 자동 빌드 성공은 실행 성공 판정이 아닙니다.
