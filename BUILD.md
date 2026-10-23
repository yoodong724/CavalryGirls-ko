# 3.1.3032 소스 빌드

Python 3.11 이상, `requirements.txt`의 패키지, PATH의 `xdelta3`, 충분한 디스크 공간이 필요합니다. 3.1.3032 / Steam 빌드 26134654의 깨끗한 게임 사본을 준비하세요. 원본 게임 파일은 소스에 포함되지 않습니다.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --profile release/reference/build26134654/build-profile.json --output work/build-r13
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --profile release/reference/build26134654/build-profile.json --build work/build-r13 --output work/package-r13
```

출력은 새 `work/` 하위 폴더에 만드세요. 빌드와 패키징은 원본 해시·현재 번역 QA/L1/L2 연결을 검사하고 원본을 읽기 전용으로 취급합니다. 생성된 package 폴더는 `CavalryGirls-Korean-r13` 폴더로 묶을 수 있습니다. 자동 검사 통과만으로 게임 실행 검증을 주장할 수 없습니다.
