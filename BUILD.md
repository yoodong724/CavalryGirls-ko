# 3.0.2950 소스 빌드

Linux 또는 WSL, Python 3.11 이상, `requirements.txt`의 패키지와 PATH의 `xdelta3`가 필요합니다. 기준 환경은 UnityPy 1.25.3, Linux xdelta3 3.0.11입니다. Windows 설치용 xdelta3 3.2.0은 `release/installer/bin/`에 포함돼 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

3.0.2950 / Steam 빌드 25429822의 깨끗한 게임 사본을 준비하세요. 입력은 읽기 전용이며 결과는 저장소의 `work/`에 생성합니다. 원본과 출력에 여러 GB의 공간이 필요합니다.

```bash
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --profile release/reference/3.0.2950/build-profile.json --output work/build-r06
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --profile release/reference/3.0.2950/build-profile.json --build work/build-r06 --output work/package-r06
```

기존 출력 폴더나 원본 해시가 다르면 중단합니다. 다시 빌드할 때는 새 출력 폴더를 사용하고 해시 검사를 우회하지 마세요. 패키징은 차분 생성 후 독립 복원 해시를 검사합니다. 결과 폴더 내용을 `CavalryGirls-Korean-r06` 폴더 안에 넣어 ZIP으로 묶으면 됩니다.

재빌드의 두 게임 파일은 다음 SHA-256과 일치해야 합니다.

| 파일 | SHA-256 |
|---|---|
| resources.assets | `08d4116485a0ca553fdf318b72c79357a7a893e9eef2d6b234f09ae0a8b170b2` |
| resources.assets.resS | `2685de66b52414d862ca2f007d19c38bb96a85b848ac3e9d044d212c9080f5f7` |

ZIP의 압축·문서·경로 메타데이터에 따라 ZIP 자체의 해시는 달라질 수 있습니다. 공개 ZIP은 로컬 검사에 사용한 후보의 설치 코드와 게임 차분을 유지하며 공개 안내와 메타데이터만 갱신합니다.

원문·번역·검수 레코드와 UI 레시피의 바이트 및 revision을 유지했습니다. 공개본에서만 개인 경로를 제거하고 빌드 입력 경로와 의존성 해시를 다시 연결했습니다. 입력을 바꾸면 검증이 중단될 수 있으며 번역 변경에는 새 검수와 화면 확인이 필요합니다.

이전 버전 2.6.2859는 [r05 태그](https://github.com/yoodong724/CavalryGirls-ko/tree/r05-public1)의 빌드 안내를 사용하세요. 실제 게임 확인 항목은 [수동 확인표](release/installer/MANUAL-TEST.ko.md)에 있습니다. 자동 빌드 성공은 게임 실행 성공 판정이 아닙니다.
