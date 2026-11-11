# Orbit (옛 Horarium, MAENG_worklog)

2026-11-11 이름을 Orbit 으로 바꿨다(DESIGN §37). 코드·문서의 옛 절과 내부 식별자(쿠키 `hor_s`, localStorage `horarium.*`, `static/horarium.ico`)에는 Horarium 이 남아 있다 — 일부러 그대로 둔 것.

연구실 출퇴근·집중도 자가기록 웹앱. 사용자는 한국어로 소통한다.

**처음이면 `HANDOFF.md` 를 먼저 읽을 것** — 이전 Claude 세션의 작업 내역, 사용자와 일하는 규칙, 이 PC 의 환경 함정, 진행 중인 장학금 조사가 들어 있다.

## 계약

- **`DESIGN.md` 가 설계 계약서다. 바꿔야 하면 이 문서를 먼저 고친다.** 새 절은 파일 끝에 다음 번호로.
- 파이썬 3.8.6(`C:\Users\PC1\AppData\Local\Programs\Python\Python38\python.exe`) + 표준 라이브러리 + pywin32 만. 외부 패키지 금지.
- `engine.py` 는 순수 함수만 — I/O 없음, win32 import 금지.
- 프론트는 바닐라 JS/SVG, 외부 리소스 0. 디자인은 DESIGN §12(카드·그림자·알약·이모지 금지, accent `#1f4fd1` 하나).
- DB 변경은 `common.SCHEMA` 에 `CREATE TABLE IF NOT EXISTS` 추가뿐.

## 절대 하지 말 것

- **샌드박스에서 이 앱을 실제 DB(`data\worklog.db`)로 띄우지 않는다.** `%LOCALAPPDATA%` 가상화 때문에 사용자 수집기와 다른 DB 사본에 써서 기록이 유실된 적이 있다. UI 확인은 정적 파일만 서빙하거나 `?mock=1` 목업으로.
- **에이전트를 띄울 때는 몇 개가 어떤 모델로 도는지 먼저 한 줄로 알린다.** 검토 3 + 검증 2 를 넘으면 먼저 묻는다. 세션 설정(ultracode)보다 이 규칙이 우선. UI 손질·작은 버그는 에이전트 없이 직접 고치고 스크린샷으로 확인.

## 테스트·배포

- 테스트: 이 폴더에서 `python -m unittest discover -s tests -t .` (400개 이상 — 끝의 OK 만 본다). 한글이 깨진 stderr 는 오류 경로 테스트의 정상 출력 — 끝의 `OK` 만 본다.
- 반영: 테스트 통과 → `POST http://127.0.0.1:8771/api/restart` → `GET /api/status`. 정적 파일은 새로고침만으로 반영.
