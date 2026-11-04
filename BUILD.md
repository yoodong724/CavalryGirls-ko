# 3.1.3080 소스 빌드

Python 3.11 이상, `requirements.txt`의 패키지, PATH의 `xdelta3`, 충분한 디스크 공간과 3.1.3080 / Steam 빌드 26425339의 깨끗한 게임 사본이 필요합니다. 원본 게임 파일은 소스에 포함되지 않습니다.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --profile release/reference/build26425339/build-profile.json --output work/build-r16
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --profile release/reference/build26425339/build-profile.json --build work/build-r16 --output work/package-r16
```

새 `work/` 출력 폴더를 사용하세요. 빌드 도구는 원본 해시와 현재 QA/L1 및 L2 또는 명시된 면제 결속을 확인합니다. 생성된 패키지는 `CavalryGirls-Korean-r16` 폴더로 묶을 수 있습니다. 격리 사본에서 한국어 타이틀, 메뉴와 빈 불러오기 화면만 확인했습니다. 신규 UI 문구 표시는 확인하지 않았습니다.
