# Orbit (옛 Horarium, MAENG_worklog)

2026-11-11 이름을 Orbit 으로 바꿨다(DESIGN §37). 코드·문서의 옛 절과 내부 식별자(쿠키 `hor_s`, localStorage `horarium.*`, `static/horarium.ico`)에는 Horarium 이 남아 있다 — 일부러 그대로 둔 것.

연구실 출퇴근·집중도 자가기록 웹앱. 사용자는 한국어로 소통한다.

**처음이면 `HANDOFF.md` 를 먼저 읽을 것** — 이전 Claude 세션의 작업 내역, 사용자와 일하는 규칙, 이 PC 의 환경 함정, 진행 중인 장학금 조사가 들어 있다.

## 계약

- **`DESIGN.md` 가 설계 계약서다. 바꿔야 하면 이 문서를 먼저 고친다.** 새 절은 파일 끝에 다음 번호로.
- 파이썬 3.8.6(`C:\Users\PC1\AppData\Local\Programs\Python\Python38\python.exe`) + 표준 라이브러리 + pywin32 만. 외부 패키지 금지.
- `engine.py` 는 순수 함수만 — I/O 없음, win32 import 금지.
- 프론트는 바닐라 JS/SVG, 외부 리소스 0(글꼴도 `static\` 에 — Pretendard · Geist). 디자인은 DESIGN §40~§43(Linear·Vercel 쪽: 어두운 화면 기본, 굵기 400·500, 강조색 없음). §12 의 accent `#1f4fd1` 은 밝은 화면에만 남아 있다.
- DB 변경은 `common.SCHEMA` 에 `CREATE TABLE IF NOT EXISTS` 추가뿐.

## 절대 하지 말 것

- **샌드박스에서 이 앱을 실제 DB(`data\worklog.db`)로 띄우지 않는다.** `%LOCALAPPDATA%` 가상화 때문에 사용자 수집기와 다른 DB 사본에 써서 기록이 유실된 적이 있다. UI 확인은 정적 파일만 서빙하거나 `?mock=1` 목업으로.
- **에이전트를 띄울 때는 몇 개가 어떤 모델로 도는지 먼저 한 줄로 알린다.** 검토 3 + 검증 2 를 넘으면 먼저 묻는다. 세션 설정(ultracode)보다 이 규칙이 우선. UI 손질·작은 버그는 에이전트 없이 직접 고치고 스크린샷으로 확인.

## 저장소

- github.com/hiyunsang/Orbit — 2026-11-11 비공개로 만들었고, 같은 날 사용자가 *"공개해도 돼"* 라고 했다(공개 전환은 깃허브 설정에서 사용자가 — Claude 의 전환 시도는 자동 권한 검사에서 막혔다). 기능 단위로 커밋하고 `git push origin main`. 메시지는 한국어로 무엇을 왜.
- **`LICENSE` 는 모든 권리 보유(All rights reserved)** — 사용자는 Orbit·Athenaeum 을 나중에 앱으로 팔 계획이고(2026-11-11), 같은 날 *"오빗에 라이선스 붙이면 되잖아"* 로 붙였다. 보기만 허락하고 사용·복사·판매는 서면 허락이 있어야 한다. 바꾸거나 MIT 같은 오픈소스 라이선스로 풀려면 먼저 묻는다(한 번 준 허락은 이미 올라간 판에서 되돌릴 수 없다).
- 남의 것을 새로 들이면 `LICENSE` 의 'Third-party files' 목록에 적는다. `logo/wordmark-o.svg` 는 맑은 고딕 윤곽이라 판매판에 넣지 않는다.
- 새로 들이는 글꼴·자료·코드는 상업적으로 써도 되는 것만(글꼴 Pretendard·Geist 는 SIL OFL — 앱에 넣어 팔아도 되고, 사용권 파일을 곁에 둔다).
- 올리지 않는 것은 `.gitignore`: `data\`(기록 DB·서랍·다른 기기 설정·로그·학사일정) · `notes\` · 사용자별 `rules/names/notices.json` · `static\_bak\` · `*.bak` · `*.lnk`. 커밋 전에 `git status` 로 확인.
- 줄바꿈은 `.gitattributes` 로 LF 고정(bat·vbs 만 CRLF).
- 공개되면 그대로 보이는 것(사용자가 알고 허락함): DESIGN §16(장학 수혜 상황) · HANDOFF(이 PC 사정·작업 기록) · 테일넷 주소(`share_tailnet.py` · HANDOFF). 그 밖의 개인 정보는 새로 적지 않는다.

## 테스트·배포

- 테스트: 이 폴더에서 `python -m unittest discover -s tests -t .` (400개 이상 — 끝의 OK 만 본다). 한글이 깨진 stderr 는 오류 경로 테스트의 정상 출력 — 끝의 `OK` 만 본다.
- 반영: 테스트 통과 → `POST http://127.0.0.1:8771/api/restart` → `GET /api/status`. 정적 파일은 새로고침만으로 반영.
