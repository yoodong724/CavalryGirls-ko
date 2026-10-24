# 3.1.3050 소스 빌드

Python 3.11 이상, `requirements.txt`의 패키지, PATH의 `xdelta3`, 충분한 디스크 공간과 3.1.3050 / Steam 빌드 26175764의 깨끗한 게임 사본이 필요합니다. 원본 게임 파일은 소스에 포함되지 않습니다.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --profile release/reference/build26175764/build-profile.json --output work/build-r15
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --profile release/reference/build26175764/build-profile.json --build work/build-r15 --output work/package-r15
```

새 `work/` 출력 폴더를 사용하세요. 빌드 도구는 원본 해시와 현재 QA/L1/L2 결속을 확인합니다. 생성된 패키지는 `CavalryGirls-Korean-r15` 폴더로 묶을 수 있습니다. 자동 검사만으로 게임 화면 표시를 검증한 것은 아닙니다.
