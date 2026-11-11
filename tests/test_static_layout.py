# -*- coding: utf-8 -*-
"""
tests/test_static_layout.py — DESIGN §18 좁은 창 2열 유지.

브라우저 없이 문자열로 확인 가능한 계약만 검사한다. 실제 렌더 검증은 §18.6 수동 체크리스트.
정규식은 공백에 관대하게 잡고, 실패 메시지에 §18 의 어느 문장을 같이 고쳐야 하는지 적는다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _block_after(text, start_idx):
    """start_idx 이후 첫 '{' 부터 짝이 맞는 '}' 까지를 돌려준다. 없으면 ''."""
    open_at = text.find("{", start_idx)
    if open_at < 0:
        return ""
    depth = 0
    for i in range(open_at, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
    return ""


class StaticLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = _read(os.path.join(common.STATIC_DIR, "app.css"))
        cls.js = _read(os.path.join(common.STATIC_DIR, "app.js"))
        cls.html = _read(os.path.join(common.STATIC_DIR, "index.html"))
        cls.design = _read(os.path.join(ROOT, "DESIGN.md"))
        cls.notices_js = _read(os.path.join(common.STATIC_DIR, "notices.js"))
        cls.server = _read(os.path.join(ROOT, "server.py"))

    # ---- §18.3 하루 보기 2열 예외 -------------------------------------
    def test_day_two_column_exception_exists(self):
        m = re.search(
            r"@media\s*\(\s*min-width:\s*560px\s*\)\s*and\s*\(\s*max-width:\s*1099px\s*\)",
            self.css)
        self.assertIsNotNone(
            m, "하루 보기 2열 예외 미디어쿼리가 사라졌다 — DESIGN §18.2/18.3")
        block = _block_after(self.css, m.end())
        self.assertIn("#main.view-day", block,
                      "예외 블록에 #main.view-day 선택자가 없다 — DESIGN §18.3")
        self.assertIn("grid-template-columns", block,
                      "예외 블록이 2열을 되돌리지 않는다 — DESIGN §18.3")
        self.assertIn("300px", block,
                      "사이드 패널 300px 이 아니다 — DESIGN §18.2 (폭을 유동으로 바꾸려면 §18.5 를 먼저 읽을 것)")

    # ---- §18.3 폴백(주·월 1열) 보존 -----------------------------------
    def test_narrow_fallback_preserved(self):
        m = re.search(r"@media\s*\(\s*max-width:\s*1099px\s*\)", self.css)
        self.assertIsNotNone(
            m, "기존 1099px 1열 블록이 사라졌다 — 주·월의 1열 규칙이자 JS 미로드 시 안전판이다 (DESIGN §18.3)")
        block = _block_after(self.css, m.end())
        self.assertIsNotNone(
            re.search(r"#main\s*\{[^}]*grid-template-columns:\s*1fr", block),
            "폴백 블록의 #main 1열 규칙이 사라졌다 — DESIGN §18.3")
        self.assertIsNotNone(
            re.search(r"#sidebar\s*\{[^}]*height:\s*auto", block),
            "폴백 블록의 #sidebar height:auto 가 사라졌다 — DESIGN §18.3")

    # ---- §18.2 헤더 축약 ----------------------------------------------
    def test_header_compact_block(self):
        m = re.search(r"@media\s*\(\s*max-width:\s*719px\s*\)", self.css)
        self.assertIsNotNone(
            m, "헤더 축약 블록이 사라졌다 — 624px 에서 헤더가 701px 로 넘친다 (DESIGN §18.1/18.2)")
        block = _block_after(self.css, m.end())
        for sel in (".brand-text", ".brand-sub"):
            self.assertIn(sel, block,
                          "%s 를 숨기지 않으면 좁은 폭 헤더가 한 줄에 안 들어간다 — DESIGN §18.2" % sel)

    # ---- §18.3 보기 클래스 토글 ---------------------------------------
    def test_view_classes_toggled_in_update_view_chrome(self):
        start = self.js.find("function updateViewChrome()")
        self.assertGreater(
            start, -1, "updateViewChrome() 가 사라졌다 — DESIGN §18.3")
        nxt = self.js.find("\n  function ", start + 1)
        body = self.js[start:nxt if nxt > 0 else len(self.js)]
        for v in ("view-day", "view-week", "view-month"):
            self.assertIn(
                "classList.toggle('%s'" % v, body,
                "'%s' 토글이 updateViewChrome() 안에 없다 — setView 의 '레이아웃 먼저, 렌더 나중' "
                "순서가 깨지면 renderTimeline 이 확정 안 된 폭을 읽는다 (DESIGN §18.3)" % v)

    # ---- §12.5 / §18.5 DOM id 유지 ------------------------------------
    def test_dom_ids_preserved(self):
        ids = [
            "main", "header", "timeline-wrap", "timeline-scroll", "timeline-inner",
            "timeline", "week-wrap", "week-scroll", "week-svg", "month-wrap",
            "month-grid", "sidebar", "card-summary", "card-reading", "phone-head", "phone-strip", "phone-more", "phone-rail", "rail-mark", "ph-todo-list", "ph-todos-n", "card-notices", "legend",
            "card-pause", "card-unknown", "card-night", "card-todos", "card-notes", "card-budget", "card-tonight", "card-vocab", "card-period", "card-events", "card-due", "legend-week", "note-pane-today",
            "notices-body", "notices-badge", "btn-notices-refresh", "notices-foot",
            "reading-body", "date-input", "status-banner",
        ]
        for i in ids:
            self.assertIn(
                'id="%s"' % i, self.html,
                "id=%s 가 사라졌다 — notices.js/reading.js/app.js 가 getElementById 로 잡는다 (DESIGN §12.5)" % i)
        self.assertRegex(
            self.html, r'class="brand-text[" ]',
            "brand-text 래퍼가 없으면 헤더 축약 CSS 가 워드마크 글자를 잡지 못한다 — DESIGN §18.3")

    # ---- §18.1 주 보기 하한 상수 (문서의 근거 숫자) ---------------------
    def test_week_col_floor_constants(self):
        gutter = re.search(r"var\s+GUTTER\s*=\s*(\d+)", self.js)
        colgap = re.search(r"var\s+COL_GAP\s*=\s*(\d+)", self.js)
        floor = re.search(
            r"Math\.max\(\s*(\d+)\s*,\s*\(\s*width\s*-\s*GUTTER\s*-\s*COL_GAP\s*\*\s*8\s*\)\s*/\s*7\s*\)",
            self.js)
        self.assertIsNotNone(gutter, "GUTTER 상수를 찾지 못했다 — DESIGN §18.1")
        self.assertIsNotNone(colgap, "COL_GAP 상수를 찾지 못했다 — DESIGN §18.1")
        self.assertIsNotNone(
            floor, "주 보기 colW 하한식이 바뀌었다 — DESIGN §18.1 의 '324px 에서 일요일이 잘린다' 근거가 무너진다")
        need = int(floor.group(1)) * 7 + int(gutter.group(1)) + int(colgap.group(1)) * 8
        self.assertEqual(
            need, 332,
            "주 보기가 하한에서 요구하는 최소 폭이 %dpx 로 바뀌었다 — DESIGN §18.1/18.2 의 근거 숫자를 같이 고칠 것" % need)

    # ---- §19 밤 일정 회귀 방지 ----------------------------------------
    def test_night_loads_exactly_where_todos_load(self):
        """밤 일정은 투두와 같은 자리에서만 불러온다 (§19.9).

        투두가 30초 자동 갱신 루프에 없는 것은 우연이 아니다 — 사용자가 고르는 중에 전체 재그리기가
        일어나면 hover·포커스가 날아간다. loadNight 를 그 루프에 넣으면 같은 회귀가 난다.
        그래서 '루프에 없는지'가 아니라 '투두와 짝이 맞는지'를 본다. 호출 지점이 어긋나면 실패한다.
        """
        body = self.js[self.js.find("function loadNight("):]
        body = body[body.find("\n  function "):]     # 정의 자신은 빼고 호출부만
        # 한 줄로 서 있는 호출만 센다(§29 부터 refreshSide 처럼 한 줄에 여럿 부르는 곳과 .then(loadTodos) 는 제외)
        todos = len(re.findall(r"^\s*loadTodos\(\);\s*$", body, re.M))
        night = len(re.findall(r"^\s*loadNight\(\);\s*$", body, re.M))
        self.assertEqual(
            night, todos,
            "loadTodos() %d곳 / loadNight() %d곳 — 짝이 안 맞는다. 밤 일정은 투두와 같은 4곳"
            "(init, changeDate, setView, 주·월 04:00 경계)에서만 불러야 하고 30초 루프에는 넣지 않는다 (§19.9)"
            % (todos, night))
        self.assertGreater(todos, 0, "loadTodos 호출부를 못 찾았다 — 이 테스트의 전제가 깨졌다")

    def test_night_band_not_clickable(self):
        """밤 밴드가 클릭을 삼키면 아래 진짜 블록을 정정할 수 없다 (§19.10)."""
        self.assertIsNotNone(
            re.search(r"\.night-band\s*,\s*\.night-label\s*\{[^}]*pointer-events:\s*none", self.css),
            "밤 밴드에 pointer-events:none 이 없다 — 5시간짜리 점선이 블록 모서리 위에 겹쳐 클릭을 삼킨다 (§19.10)")
        self.assertIn("fill: 'none'", self.js,
                      "밤 밴드를 채우면 아래 기록을 지운다 (§19.10)")

    def test_night_not_in_spans(self):
        """day.spans 에 밤 블록을 넣으면 정정·주 보기·요약이 오염된다 (§19.10)."""
        i = self.js.find("function nightBand(")
        self.assertGreater(i, -1, "nightBand() 가 사라졌다 (§19.9)")
        body = self.js[i:self.js.find("\n  function ", i + 1)]
        self.assertNotIn("data-idx", body,
                         "밤 밴드에 data-idx 가 붙으면 클릭 핸들러가 이것을 정정 대상으로 본다 (§19.10)")

    # ---- 문서와 코드를 같이 움직이게 하는 장치 -------------------------
    def test_design_18_documented(self):
        self.assertIsNotNone(
            re.search(r"^##\s*18\.", self.design, re.M),
            "DESIGN.md 에 §18 이 없다 — DESIGN.md:4 '바꿔야 하면 이 문서를 먼저 고친다'")
        sec = self.design[self.design.index("## 18."):]
        self.assertIn("### 18.6 검증", sec, "§18 에 검증 절차가 없다")
        self.assertNotIn(
            "1100px 미만이면 사이드 패널이 아래로 내려가는 기존 동작 유지", self.design,
            "§12.5 의 옛 반응형 문장이 남아 있다 — §18 과 모순된다")

    def test_sidebar_order_design_20(self):
        ids = re.findall(r'<section class="side-sec" id="(card-[a-z]+)"', self.html)
        self.assertEqual(ids, [
            "card-tonight", "card-due", "card-period", "card-vocab", "card-anki", "card-todos", "card-notes", "card-budget", "card-night", "card-drawer",
            "card-events", "card-notices", "card-unknown", "card-pause", "card-devices", "card-reading",
        ], "사이드바 카드 순서가 §20.2·§21.5·§22.2·§23.2·§23.3 과 다르다")

    def test_period_design_23_3(self):
        self.assertIn("### 23.3", self.design)
        self.assertIn("#main.view-day #card-period { display: none; }", self.css)
        for cid in ("card-todos", "card-notes", "card-budget", "card-night", "card-vocab", "card-tonight"):
            self.assertIn("#main.view-week #" + cid, self.css, cid + " 는 주 보기에서 숨겨야 한다 (§23.3)")
        self.assertIn("#main > .day-summary { display: flex; }", self.css, "요약 띠는 모든 보기에 (§23.3)")
        self.assertIn("loadPeriod(p, view, seq);", self.js, "기간 판 조회는 loadRange 성공 콜백에서 (§23.3)")

    def test_day_layout_design_23(self):
        self.assertIsNotNone(re.search(r"^##\s*23\.", self.design, re.M), "DESIGN.md 에 §23 이 없다")
        self.assertIn("### 23.2", self.design)
        # 요약은 사이드바 밖, #main 의 첫 자식
        main_i = self.html.index('<main id="main">'); side_i = self.html.index('<aside id="sidebar">')
        sum_i = self.html.index('id="card-summary"')
        self.assertTrue(main_i < sum_i < side_i, "요약 한 줄은 #main 직속, 사이드바 앞이어야 한다 (§23.2)")
        self.assertNotIn('id="btn-vocab" type="button" title="Athenaeum 단어 맞추기를 작은 창으로 엽니다 (§20.8)">단어<', self.html,
                         "헤더의 '단어' 는 없애고 카드 한 칸으로 (§23.2)")
        self.assertIn('id="card-vocab"', self.html)
        blocks = re.findall(r"@media\s*\(\s*min-width:\s*1100px\s*\)\s*\{(.*?)\n\}", self.css, re.S)
        day = [b for b in blocks if "grid-template-columns: 380px" in b]   # §23.3 이 같은 조건의 주·월 블록을 하나 더 둔다
        self.assertTrue(day, "§23.2 넓은 창 하루 블록(타임라인 380px)이 없다")
        self.assertIn('grid-template-areas', day[0])

    def test_tonight_design_22(self):
        self.assertIsNotNone(re.search(r"^##\s*22\.", self.design, re.M), "DESIGN.md 에 §22 가 없다")
        self.assertIn("### 22.4 검증", self.design[self.design.index("## 22."):])
        loop = self.js[self.js.index("DAY_REFRESH_MS);") - 1500:self.js.index("DAY_REFRESH_MS);")]
        self.assertIn("updateTonight();", loop, "30초 루프에서 updateTonight 를 불러야 16시를 넘길 때 블록이 뜬다 (§22.2)")
        self.assertNotIn("renderTonight(", loop, "루프에서는 hidden 만 바꾼다 — 재그리기 금지 (§22.2)")
        # 루프 안의 loadNight 는 주·월 보기의 04:00 경계 재조회(§19.9 의 4곳 중 하나)라 여기서는 막지 않는다 —
        # 호출 지점 수는 test_night_loads_exactly_where_todos_load 가 지킨다
        self.assertIn("night_prompt_hour", common.DEFAULT_CONFIG)
        import server
        self.assertIn("night_prompt_hour", server._display_config())

    def test_budget_loads_where_night_loads(self):
        night = len(re.findall(r"^\s*loadNight\(\);\s*$", self.js, re.M))
        budget = len(re.findall(r"^\s*loadBudget\(\);\s*$", self.js, re.M))
        self.assertEqual(night, budget, "loadBudget 는 loadNight 와 같은 곳(4곳)에서 불러야 한다 (§21.5)")
        self.assertGreater(night, 0)
        self.assertNotIn("loadBudget", self.js[self.js.index("DAY_REFRESH"):self.js.index("DAY_REFRESH") + 2000]
                         if "DAY_REFRESH" in self.js else "", "30초 갱신 루프에 예산 조회를 넣지 않는다 (§21.5)")

    def test_design_21_documented(self):
        self.assertIsNotNone(
            re.search(r"^##\s*21\.", self.design, re.M),
            "DESIGN.md 에 §21(예산) 이 없다 — DESIGN.md:4 '바꿔야 하면 이 문서를 먼저 고친다'")
        sec = self.design[self.design.index("## 21."):]
        self.assertIn("### 21.7 검증", sec, "§21 에 검증 절차가 없다")

    def test_notices_row_cap_design_20(self):
        self.assertIn("var ROW_CAP = 4;", self.notices_js,
                      "notices.js 의 행 상한(§20.2: 접힌 상태 4행)이 없다")

    def test_design_20_documented(self):
        self.assertIsNotNone(
            re.search(r"^##\s*20\.", self.design, re.M),
            "DESIGN.md 에 §20(할 일을 맨 위로) 이 없다 — DESIGN.md:4 '바꿔야 하면 이 문서를 먼저 고친다'")
        sec = self.design[self.design.index("## 20."):]
        self.assertIn("### 20.7 검증", sec, "§20 에 검증 절차가 없다")

    def test_schedule_design_24(self):
        self.assertIsNotNone(re.search(r"^##\s*24\.", self.design, re.M), "DESIGN.md 에 §24 가 없다")
        n_b = len(re.findall(r"^\s*loadBudget\(\);\s*$", self.js, re.M))
        n_s = len(re.findall(r"^\s*loadSchedule\(\);\s*$", self.js, re.M))
        self.assertEqual(n_b, n_s, "loadSchedule 는 loadBudget 과 같은 4곳에서 (§24.3)")

    def test_two_devices_design_27(self):
        self.assertIsNotNone(re.search(r"^##\s*27\.", self.design, re.M), "DESIGN.md 에 §27 이 없다")
        self.assertIn("### 27.4 검증", self.design[self.design.index("## 27."):])
        self.assertIn("window.addEventListener('focus', refreshSide);", self.js, "창에 돌아오면 옆 판을 다시 받아야 한다 (§27.2)")
        self.assertIn("visibilitychange", self.js)
        self.assertIn("!document.hasFocus()", self.js, "주기 갱신은 포커스 없는 창만 (§27.2)")
        self.assertIn("location.hostname + ':8770/vocab/quiz'", self.js, "단어 팝업은 페이지를 연 호스트를 따라가야 한다 (§27.3)")
        self.assertNotIn("'http://127.0.0.1:8770/vocab/quiz'", self.js)
        self.assertIn("body.base = n.ver", self.js, "메모 저장은 불러온 판을 같이 보낸다 (§27.2)")

    def test_events_design_28(self):
        self.assertIsNotNone(re.search(r"^##\s*28\.", self.design, re.M), "DESIGN.md 에 §28 이 없다")
        self.assertIn("### 28.5 검증", self.design[self.design.index("## 28."):])
        n_s = len(re.findall(r"^\s*loadSchedule\(\);\s*$", self.js, re.M))
        n_e = len(re.findall(r"^\s*loadEvents\(\);\s*$", self.js, re.M))
        self.assertEqual(n_s, n_e, "loadEvents 는 loadSchedule 과 같은 4곳에서 (§28)")
        self.assertIn("#main.view-week #card-events", self.css, "주·월에서는 일정 카드를 숨긴다 (§28.1 — 기간 판이 대신 보여 준다)")

    def test_zones_design_29(self):
        self.assertIsNotNone(re.search(r"^##\s*29\.", self.design, re.M), "DESIGN.md 에 §29 가 없다")
        for z in ("zone-mine", "zone-coming", "zone-school", "zone-tools"):
            self.assertIn('id="%s"' % z, self.html, z + " 구역 머리가 없다 (§29.1)")
        self.assertNotIn('id="card-schedule"', self.html, "학사 일정 카드는 타임라인에 합쳤다 (§29.3)")
        self.assertNotIn('id="event-input"', self.html, "입력은 할 일 칸 하나 (§29.2)")
        self.assertIn("var NEEDS_PARSE", self.js)
        self.assertIn("function renderAgenda()", self.js)

    def test_small_four_design_30(self):
        self.assertIsNotNone(re.search(r"^##\s*30\.", self.design, re.M), "DESIGN.md 에 §30 이 없다")
        self.assertIn('rel="manifest"', self.html)
        self.assertNotIn('id="card-legend"', self.html, "범례는 타임라인 아래 띠로 (§30)")
        self.assertEqual(self.html.count('class="legend-bar"'), 2, "하루·주 두 곳에 범례 띠")
        self.assertIn('data-note="pinned"', self.html, "메모는 오늘/고정 탭 (§30)")
        self.assertIn("function autogrowNote", self.js)
        self.assertIn("clientWidth < 80", self.js, "숨은 탭에서 autogrow 폭발 방지 (HANDOFF §1.2)")
        self.assertEqual([k["id"] for k in common.NIGHT_KINDS], ["appointment", "english", "workout", "study", "research", "rest"])

    def test_anki_design_31(self):
        self.assertIsNotNone(re.search(r"^##\s*31\.", self.design, re.M), "DESIGN.md 에 §31 이 없다")
        self.assertIn('id="card-anki"', self.html)
        self.assertIn("#main.view-week #card-anki", self.css, "주·월에서는 Anki 카드를 숨긴다")
        self.assertIn("function renderAnki()", self.js)

    def test_todo_weight_design_32(self):
        self.assertIsNotNone(re.search(r"^##\s*32\.", self.design, re.M), "DESIGN.md 에 §32 가 없다")
        self.assertIn('id="card-anki" hidden', self.html, "§31.6 Anki 카드는 숨긴 채 시작한다")
        self.assertIn("function ankiOn()", self.js)
        self.assertIn('"mine mine"', self.css, "§34 넓은 창에서 내 것은 한 영역(안에서 두 칸)")
        self.assertIn('"todos notes"', self.css, "§34 내 것 안: 할 일 왼쪽, 메모 오른쪽")
        self.assertNotIn("노랭이 · 초록이 · 작은 창으로 열림", self.html, "§32 단어 카드는 버튼 한 줄")
        self.assertIn("#card-todos .todo-text, #card-todos .evt-t { font-size: 15px; }", self.css)
        self.assertIn('placeholder="할 일 · 마감 · 일정 (예: 24일 숙제 / THK 문의)"', self.html)
        self.assertIn('"anki_card": bool(CFG.get("anki_card", False))', self.server)

    def test_todo_visibility_design_33(self):
        self.assertIsNotNone(re.search(r"^##\s*33\.", self.design, re.M), "DESIGN.md 에 §33 이 없다")
        self.assertIn("#card-todos > .sec { font-size: 14px; font-weight: 500;", self.css, "§33 할 일 제목은 한 급 위")
        self.assertIn("#card-todos #todo-input { background: var(--surface); border: 1px solid var(--line-2);", self.css,
                      "§33 판 위 입력칸은 흰 바탕 + 테두리")
        self.assertIn("#card-todos #todo-input:focus { border-color: var(--accent); }", self.css,
                      "§33 id 둘짜리 선택자가 #todo-input:focus 를 이기므로 포커스 규칙을 따로 둔다")
        self.assertIn("#card-todos #todo-input::placeholder { color: var(--text-3); }", self.css)
        self.assertIsNotNone(re.search(r"^###\s*33\.4", self.design, re.M), "DESIGN.md 에 §33.4(블록) 가 없다")
        self.assertIn("#card-todos { margin: 0 calc(-1 * var(--s3)); padding: 0 var(--s3) var(--s3); background: var(--surface); "
                      "border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); border-left: 3px solid var(--accent); }",
                      self.css, "§33.4 할 일은 판 가장자리까지 닿는 흰 블록")
        self.assertIn("#card-vocab { border-bottom: 0; }", self.css, "§33.4 블록 윗선과 겹선 방지")
        self.assertIn("#main.view-day > #sidebar #card-todos { margin: var(--s3) 0 0 calc(-1 * var(--s6)); padding-left: var(--s6); }", self.css, "§33.4·§34 넓은 사이드바에선 왼쪽으로만 가장자리까지")
        self.assertNotIn("#card-todos { box-shadow", self.css, "§12.1 그림자 금지")

    def test_two_columns_design_34(self):
        self.assertIsNotNone(re.search(r"^##\s*34\.", self.design, re.M), "DESIGN.md 에 §34 가 없다")
        i_open = self.html.index('<div id="mine-body">')
        i_vocab = self.html.index('id="card-vocab"')
        i_night = self.html.index('id="card-night"')
        i_close = self.html.index('</div><!-- /mine-body §34 -->')
        i_coming = self.html.index('id="zone-coming"')
        self.assertTrue(i_open < i_vocab < i_night < i_close < i_coming, "§34 mine-body 는 단어~밤 여섯 카드만 감싼다")
        self.assertIn('#main.view-day > #sidebar > #mine-body { grid-area: mine; display: grid;', self.css)
        self.assertIn('"todos vocab" "todos anki" "todos notes" "todos budget" "todos night" "todos drawer" "todos ."', self.css, "§34·§35 안쪽 격자 영역")
        self.assertIn('grid-template-rows: repeat(6, auto) minmax(0px, 1fr);', self.css, "§34 마지막 1fr 줄이 블록의 남는 높이를 받는다")
        self.assertIn('grid-template-columns: minmax(0px, 3fr) minmax(280px, 2fr)', self.css, "§34 창 폭을 쓰는 두 칸")
        self.assertNotIn('repeat(2, minmax(0px, 360px))', self.css)

    def test_drawer_design_35(self):
        self.assertIsNotNone(re.search(r"^##\s*35\.", self.design, re.M), "DESIGN.md 에 §35(서랍) 가 없다")
        i_night = self.html.index('id="card-night"')
        i_drawer = self.html.index('id="card-drawer"')
        i_close = self.html.index('</div><!-- /mine-body §34 -->')
        self.assertTrue(i_night < i_drawer < i_close, "§35 서랍은 내 것 안, 밤 일정 다음")
        for i in ("btn-drawer-add", "drawer-add", "drawer-label", "drawer-value", "btn-drawer-save", "btn-drawer-file", "drawer-file", "btn-drawer-cancel", "drawer-list"):
            self.assertIn('id="%s"' % i, self.html, "§35 id=%s 가 없다 — app.js 가 getElementById 로 잡는다" % i)
        self.assertIn("#main.view-day #card-drawer { grid-area: drawer; }", self.css)
        self.assertIn("#main.view-week #card-drawer, #main.view-month #card-drawer", self.css, "§35 주·월에서는 내 것과 같이 숨는다")
        self.assertIn(".drawer-row.copied .drawer-v", self.css)
        for fn in ("function loadDrawer()", "function copyText(", "function copyImage(", "function renderDrawer()", "function saveDrawer()"):
            self.assertIn(fn, self.js, "§35 app.js 에 %s 가 없다" % fn)
        self.assertIn("p === '/api/drawer'", self.js, "§35 목업에 /api/drawer 가 없다")
        self.assertIn('("GET", "/api/drawer/file"): api_drawer_file', self.server)
        self.assertIn("class Raw(object):", self.server, "§35.2 바이트 응답 규약")
        self.assertIn("isinstance(result, Raw)", self.server)
        self.assertIn("CREATE TABLE IF NOT EXISTS drawer(", _read(os.path.join(ROOT, "common.py")))

    def test_devices_design_36(self):
        self.assertIsNotNone(re.search(r"^##\s*36\.", self.design, re.M), "DESIGN.md 에 §36(다른 기기) 이 없다")
        self.assertIn('<section class="side-sec" id="card-devices" hidden>', self.html, "§36.4 설정 카드는 숨긴 채 시작(밖에서 열면 계속 숨김)")
        self.assertIn('id="link-logout"', self.html)
        self.assertIn('apple-mobile-web-app-capable', self.html)
        login = _read(os.path.join(common.STATIC_DIR, "login.html"))
        self.assertIn("fetch('/api/login'", login)
        self.assertIn("/^\\/(?![\\/\\\\])/.test(n)", login, "§36.4 next 는 이 사이트 안 경로만")
        self.assertNotIn("http://", login.replace("http://www.w3.org", ""), "로그인 화면은 외부 리소스 0")
        self.assertIn("r.status === 401 && j && j.login", self.js, "§36.2 api() 가 401 login 을 받으면 로그인 화면으로")
        self.assertIn("window.open('http://' + location.hostname + ':8770/vocab/quiz'", self.js, "§36.3 단어장 팝업은 http 고정")
        self.assertIn("function loadDevices(", self.js)
        self.assertIn("p === '/api/devices'", self.js, "§36 목업")
        self.assertIn("#main.phone-pages > #sidebar { order: 2;", self.css, "§36.5 → §39.2: 폰에서는 화면 머리·히어로 다음에 판")
        self.assertIn("@media (hover: none) and (pointer: coarse)", self.css, "§36.5 터치 기기 16px")
        self.assertIn('"unknown pause" "devices devices"', self.css)
        self.assertIn(".is-remote #link-restart, .is-remote .drawer-reveal { display: none; }", self.css)

    def test_orbit_design_37(self):
        self.assertIsNotNone(re.search(r"^##\s*37\.", self.design, re.M), "DESIGN.md 에 §37(Orbit) 이 없다")
        self.assertIn("<title>Orbit · 출근기록</title>", self.html, "§37.3 창 제목에 '출근기록' 을 남긴다 — 수집기 자기 인식 규칙")
        self.assertIn('<img class="brand-mark" src="/static/logo-mark.svg" alt=""><span class="brand-text">ORBIT</span>', self.html,
                      "§37.8 마크 옆에 글자 ORBIT (아테네움처럼)")
        self.assertIn('font-family: "Malgun Gothic", "Segoe UI"', self.css, "§37.8 글자는 §36 안내 화면과 같은 맑은 고딕")
        self.assertNotIn(">HORARIUM<", self.html)
        login = _read(os.path.join(common.STATIC_DIR, "login.html"))
        self.assertIn('<h1><img class="brand-mark" src="/static/logo-mark.svg" alt="">ORBIT</h1>', login)
        self.assertNotIn("Horarium", login)
        man = _read(os.path.join(common.STATIC_DIR, "manifest.json"))
        self.assertIn('"name": "Orbit"', man)
        self.assertFalse(os.path.exists(os.path.join(common.STATIC_DIR, "wordmark.svg")), "§37.8 마크가 O 인 워드마크는 화면에서 은퇴")
        for f in ("logo-mark.svg", "favicon.svg"):
            svg = _read(os.path.join(common.STATIC_DIR, f))
            self.assertNotIn("#1f4fd1", svg, "%s: 마크는 잉크 한 색 (§37.6 미니멀)" % f)
            ink = "#f2f2ef" if f == "favicon.svg" else "#1c1c1a"      # §39.1 파비콘은 검은 바탕 + 밝은 마크
            self.assertEqual(svg.count('fill="%s"/>' % ink), 2, "%s: 가운데 원 + 위성 (§37.7)" % f)
            self.assertNotIn("<text", svg, "%s: 글자는 윤곽 경로 — 아이폰에 맑은 고딕이 없다 (§37.1)" % f)
        for rel in ("rules.default.json", "names.default.json"):
            self.assertIn("Orbit · 출근기록", _read(os.path.join(ROOT, rel)), "%s: 이 앱 자신을 알아보는 규칙 (§37.3)" % rel)
        self.assertIn("<img src='/static/logo-mark.svg' alt='' style='height:20px;filter:invert(1)'>ORBIT</h3>", self.server, "§37.8 꺼짐 안내도 마크 옆에 글자(§39.1 검은 바탕)")

    def test_minute_hand_design_37_9(self):
        self.assertIsNotNone(re.search(r"^###\s*37\.9", self.design, re.M), "DESIGN.md 에 §37.9(분침) 이 없다")
        svg = _read(os.path.join(common.STATIC_DIR, "logo-mark-clock.svg"))
        self.assertIn('class="om-moon"', svg)
        self.assertIn('class="om-gap"', svg, "고리의 틈도 위성과 같이 돌아야 한다")
        self.assertIn("function initOrbitMark()", self.js)
        self.assertIn("initOrbitMark();", self.js)
        self.assertIn("(d.getMinutes() + d.getSeconds() / 60) * 6", self.js, "§37.9 한 시간에 한 바퀴(분침)")
        self.assertIn('"logo-mark-clock.svg"', self.server, "로그인 화면도 쓰므로 로그인 전에 준다")
        self.assertIn("logo-mark-clock.svg", _read(os.path.join(common.STATIC_DIR, "login.html")))
        self.assertIn("#header h1 .brand-mark { height: 20px; width: 20px;", self.css, "끼워 넣은 svg 는 width:auto 가 300px 로 퍼진다")

    def test_phone_app_design_39(self):
        self.assertIsNotNone(re.search(r"^##\s*39\.", self.design, re.M), "DESIGN.md 에 §39 가 없다")
        # 39.1 테마
        self.assertIn("localStorage.getItem('orbit.theme')||'dark'", self.html, "§39.1 기본은 어둡게 — <head> 에서 CSS 보다 먼저")
        self.assertLess(self.html.index("orbit.theme"), self.html.index('href="/static/app.css"'))
        self.assertIn(':root[data-theme="dark"] { --bg: #08090a;', self.css, "§42 거의 검정(Linear)")
        self.assertIn("function themedCats(list)", self.js)
        self.assertIn("state.categories = themedCats(day.categories)", self.js, "§39.1 밝은 회색 카테고리는 어두운 화면에서 어둡게")
        self.assertIn('id="link-theme"', self.html)
        # 39.2 폰
        js_pages = self.js[self.js.index("var PHONE_PAGES"):self.js.index("var PHONE_PAGES") + 220]
        for page in ("today", "timeline", "todos", "events", "more", "notes", "night", "drawer", "notices", "tools"):
            self.assertIn("'%s'" % page, js_pages)
        for sec in ("today", "timeline", "todos", "events", "notes", "night", "drawer", "notices", "tools", "more"):
            self.assertIn('<a href="#/%s" data-sec="%s">' % (sec, sec), self.html, "§41 오른쪽 세로 바 %s" % sec)
        self.assertNotIn('id="phone-menu"', self.html, "§38 의 왼쪽 메뉴는 없다")
        self.assertNotIn('id="phone-tabs"', self.html, "§41 아래 탭은 오른쪽 세로 바로 대체")
        self.assertNotIn('id="phone-fab"', self.html, "§40 동그란 더하기 단추는 없다")
        self.assertIn("window.addEventListener('hashchange', applyPhonePage)", self.js, "뒤로 가기가 앞 화면으로")
        self.assertIn("replace(/^#\\/?/, '')", self.js, "화면 이름은 #/이름 — id 와 겹쳐 브라우저가 스크롤하지 않게")
        self.assertIn("renderPhoneStrip(spans, startHour, endHour, today, now);", self.js)
        self.assertIn("function renderTasksPhone()", self.js)
        self.assertIn("renderPhoneHero();", self.js)
        m = re.search(r"/\* ── §39\.2 폰.*", self.css, re.S)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("@media (max-width: 559px) {", block, "§39.2 규칙은 559px 이하에만 — 1/3 창(624px)의 배치는 그대로")
        self.assertIn(".phone-head, .phone-hero, .phone-sec, .phone-rail, .ph-list, .tile-pv { display: none; }", block, "넓은 화면에서는 폰 전용 요소가 안 보인다")
        self.assertIn("#main.phone-pages.pg-home #card-tonight:not([hidden])", block, "JS 가 숨긴 카드는 계속 숨는다")
        self.assertNotIn("#main.phone-pages.pg-home #card-due", block, "§39.2 '곧' 칸은 폰에서 숨긴다(목록 맨 위와 겹침)")

    def test_vercel_direction_design_41(self):
        self.assertIsNotNone(re.search(r"^##\s*40\.", self.design, re.M), "DESIGN.md 에 §40 이 없다")
        self.assertIsNotNone(re.search(r"^##\s*41\.", self.design, re.M), "DESIGN.md 에 §41 이 없다")
        # 굵기는 400·500 둘 — 600 이상은 스타일시트 어디에도 없다(글꼴 파일의 범위 선언 한 줄은 예외)
        heavy = [w for w in re.findall(r"font-weight:\s*(\d+)\s*[;}]", self.css) if int(w) >= 600]
        self.assertEqual(heavy, [], "§41 굵은 글씨(600 이상)를 쓰지 않는다")
        dark = self.css[self.css.index(':root[data-theme="dark"] { --bg: #08090a;'):]
        dark = dark[:dark.index("}")]
        self.assertIn("--accent: #f7f8f8;", dark, "§41 강조색 없음 — 밝은 글자색이 강조")
        self.assertIn("--r: 6px;", dark, "§42 모서리는 Linear 처럼 작게(§41 의 0 을 대체)")
        m = re.search(r"/\* ── §39\.2 폰.*", self.css, re.S)
        block = m.group(0)
        self.assertIn(".phone-rail { display: flex; flex-direction: column; position: fixed;", block, "§41 세로 바는 화면에 붙어 따라다닌다")
        self.assertIn("writing-mode: vertical-rl; text-orientation: upright;", block, "§41 세로 바 글자는 세로로")
        self.assertIn("#main.phone-pages > #sidebar > #mine-body { display: contents !important; }", block, "§41 한 장 안에서 칸의 차례를 섞는다")
        self.assertNotIn("var(--accent)", block, "§41 폰 규칙은 강조색을 쓰지 않는다")
        for fn in ("function markRail(p)", "function syncRail()", "function phoneSpy()", "function phoneScrollTo(p, smooth)"):
            self.assertIn(fn, self.js)
        self.assertIn("m.classList.toggle('pg-home', !tl);", self.js, "§41 타임라인만 따로 화면, 나머지는 한 장")
        self.assertNotIn("pt-list", self.js, "§41 '오늘' 화면의 줄인 할 일 목록은 없다 — 전체 목록이 바로 아래에 있다")

    def test_linear_orbit_design_42(self):
        self.assertIsNotNone(re.search(r"^##\s*42\.", self.design, re.M), "DESIGN.md 에 §42 가 없다")
        # 로고만 · 시작 화면
        self.assertIn("#header h1 .brand-text { display: none; }", self.css, "§42 글자 ORBIT 은 감춘다")
        self.assertIn('<div id="splash" aria-hidden="true">', self.html)
        self.assertIn("sessionStorage.getItem('orbit.splash')", self.html, "§42 시작 화면은 한 번 연 창에서 다시 안 나온다")
        self.assertLess(self.html.index("orbit.splash"), self.html.index('href="/static/app.css"'))
        for kf in ("@keyframes sp-planet", "@keyframes sp-ring", "@keyframes sp-moon", "@keyframes splash-out"):
            self.assertIn(kf, self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { #splash { display: none; } }", self.css)
        self.assertIn("var sp = $('splash'); if (sp && sp.parentNode) sp.parentNode.removeChild(sp);", self.js, "움직임이 멈춘 창에서도 시작 화면을 걷어 낸다")
        # 하루 궤도
        self.assertIn('<svg id="orbit-svg" viewBox="0 0 240 240"', self.html)
        for fn in ("function todoProgress()", "function orbitArc(r, d0, d1)", "function renderPhoneStrip(spans, startHour, endHour, today, now)"):
            self.assertIn(fn, self.js)
        self.assertNotIn("ps-bar", self.js, "§42 가로 하루 띠는 궤도로 대체")
        for cls in (".ob-track", ".ob-arc", ".ob-prog", ".ob-now"):
            self.assertIn(cls + " {", self.css)
        # 두 칸 타일
        m = re.search(r"/\* ── §39\.2 폰.*", self.css, re.S)
        block = m.group(0)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr)); grid-auto-flow: dense;", block, "§42 폰에서 두 칸씩")
        self.assertIn("#main.phone-pages #sidebar .side-sec.tile:not(.open) > :not(.sec):not(.tile-pv) { display: none !important; }", block)
        self.assertIn("#main.phone-pages #sidebar .side-sec.open { grid-column: 1 / -1; }", block, "펼친 칸은 두 칸을 다 쓴다")
        self.assertIn(".phone-head, .phone-hero, .phone-sec, .phone-rail, .ph-list, .tile-pv { display: none; }", block, "넓은 화면에서는 미리 보기가 안 보인다")
        for fn in ("function initTiles()", "function renderTiles()", "function openTile(id, open)"):
            self.assertIn(fn, self.js)
        self.assertIn("state.railPin", self.js, "짧은 화면에서도 세로 바로 간 칸이 '지금 칸'으로 남는다")
        # 앱 아이콘: 순검정 바탕
        gen = io.open(os.path.join(ROOT, "logo", "make_orbit.py"), encoding="utf-8").read()
        self.assertIn('ICON_BG, ICON_INK = "#000000", "#f2f2ef"', gen)

    def test_design_19_documented(self):
        self.assertIsNotNone(
            re.search(r"^##\s*19\.", self.design, re.M),
            "DESIGN.md 에 §19(밤 일정) 가 없다 — DESIGN.md:4 '바꿔야 하면 이 문서를 먼저 고친다'")
        sec = self.design[self.design.index("## 19."):]
        self.assertIn("### 19.11 검증", sec, "§19 에 검증 절차가 없다")


if __name__ == "__main__":
    unittest.main()
