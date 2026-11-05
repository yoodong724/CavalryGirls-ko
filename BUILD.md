# 3.1.3084 소스 빌드

Python 3.11 이상, `requirements.txt`의 패키지, PATH의 `xdelta3`, 충분한 디스크 공간과 3.1.3084 / Steam 빌드 26426685의 깨끗한 게임 사본이 필요합니다. 게임 원본은 소스에 포함되지 않습니다.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
read -r -p '깨끗한 게임 사본의 절대 경로: ' CGKO_SOURCE
python tools/maintenance/build.py --source-game "$CGKO_SOURCE" --profile release/reference/build26426685/build-profile.json --output work/build-r17
python tools/maintenance/package.py --source-game "$CGKO_SOURCE" --profile release/reference/build26426685/build-profile.json --build work/build-r17 --output work/package-r17
```

새 `work/` 출력 폴더를 사용하세요. 빌드 도구는 원본 해시와 이월된 승인 결속을 확인합니다.

게임 실행 화면은 확인하지 않았습니다. 저장된 게임 불러오기, 대사·선택지, 전투 HUD, 드문 UI 상태와 DLC 전체는 미확인입니다. Enc/Cloth는 번역 범위에서 제외됩니다.
