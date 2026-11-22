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
        cls.common = _read(os.path.join(ROOT, "common.py"))
        cls.plans = _read(os.path.join(ROOT, "plans.py"))
        cls.inbox = _read(os.path.join(ROOT, "inbox.py"))

    # ---- §18.3 하루 보기 2열 예외 → §49 에서 폰 화면 한 줄(가운데)로 바뀜 ----
    def test_day_two_column_exception_exists(self):
        m = re.search(
            r"@media\s*\(\s*min-width:\s*560px\s*\)\s*and\s*\(\s*max-width:\s*1099px\s*\)",
            self.css)
        self.assertIsNotNone(m, "560~1099px 블록이 없다 — DESIGN §49")
        block = _block_after(self.css, m.end())
        self.assertIn("#main.phone-pages.pg-home { grid-template-columns: minmax(0px, 600px); justify-content: center; }", block,
                      "§49 중간 폭은 폰 화면 한 줄을 가운데(최대 600)")
        self.assertNotIn("#main.view-day { grid-template-columns: 260px", self.css, "§49 옛 하루 2열(260px) 은 없앴다")

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
            "main", "header", "orbit-box", "orbit-svg", "oz-all", "ow-days", "om-wrap", "om-grid",          # §55 직선 타임라인 요소는 없다 — 궤도가 타임라인
            "sidebar", "card-summary", "card-reading", "phone-head", "phone-strip", "phone-more", "phone-rail", "rail-mark", "ph-todo-list", "ph-todos-n", "card-notices",
            "card-pause", "card-unknown", "card-night", "card-todos", "card-notes", "card-budget", "card-tonight", "card-vocab", "card-period", "card-events", "card-due", "note-pane-today",
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
        """밤 계획이 클릭을 삼키거나 기록을 덮으면 아래 진짜 구간을 정정할 수 없다 (§19.10 → §55 궤도의 점선 호)."""
        m = re.search(r"\.ob-night\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, "궤도의 밤 계획 호(.ob-night) 규칙이 없다")
        self.assertIn("fill: none", m.group(1), "밤 계획 호를 채우면 기록을 덮는다 (§19.10)")
        self.assertIn("pointer-events: none", m.group(1), "밤 계획 호가 구간 누르기를 삼키지 않는다 (§19.10)")

    def test_night_not_in_spans(self):
        """day.spans 에 밤 블록을 넣으면 정정·주 보기·요약이 오염된다 (§19.10) — §55 궤도에서는 점선 호로만."""
        i = self.js.find("// 밤 계획(§19): 그 날 고리 바깥의 점선 호")
        self.assertGreater(i, -1, "궤도의 밤 계획 호가 사라졌다 (§19.9 · §55)")
        body = self.js[i:self.js.find("// 위성 = 지금", i)]
        self.assertIn("class: 'ob-night'", body)
        self.assertNotIn("data-i", body, "밤 호에 data-i 가 붙으면 누르기가 이것을 정정 대상으로 본다 (§19.10)")

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
            "card-tonight", "card-due", "card-period", "card-vocab", "card-anki", "card-todos", "card-keeps", "card-notes", "card-budget", "card-night", "card-drawer",
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
        # §49: 넓은 창의 하루 블록(타임라인 380px + 판 격자)은 없애고 나란히(왼쪽 타임라인 · 오른쪽 폰 한 장)로 바꿨다
        self.assertNotIn("grid-template-columns: 380px", self.css)
        self.assertIn("#main.phone-pages.pg-split { width: var(--home-w);", self.css)

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
        # §51: 날짜 낱말로 가르던 프론트 정규식은 서버(inbox.NEEDS_PARSE — claude 가 없을 때만)로 옮겼다
        self.assertNotIn("var NEEDS_PARSE", self.js)
        self.assertIn("NEEDS_PARSE = re.compile(", self.inbox)
        self.assertIn("function renderAgenda()", self.js)

    def test_small_four_design_30(self):
        self.assertIsNotNone(re.search(r"^##\s*30\.", self.design, re.M), "DESIGN.md 에 §30 이 없다")
        self.assertIn('rel="manifest"', self.html)
        self.assertNotIn('id="card-legend"', self.html, "범례는 타임라인 아래 띠로 (§30)")
        self.assertEqual(self.html.count('class="legend-bar"'), 0, "§55 범례 띠는 없다 — 궤도 안의 분류별 시간이 범례")
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
        self.assertNotIn('"mine mine"', self.css, "§49 §34 의 넓은 창 두 칸 격자는 없앴다")
        self.assertNotIn("노랭이 · 초록이 · 작은 창으로 열림", self.html, "§32 단어 카드는 버튼 한 줄")
        self.assertIn("#card-todos .todo-text, #card-todos .evt-t { font-size: 15px; }", self.css)
        self.assertIn('<textarea id="todo-input" rows="1" placeholder="떠오르는 대로 — 할 일 · 일정 · 기억할 것"', self.html, "§51 받아 적기 칸")
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
        self.assertNotIn("#main.view-day > #sidebar #card-todos { margin: var(--s3)", self.css, "§49 옛 넓은 사이드바는 없앴다")
        self.assertNotIn("#card-todos { box-shadow", self.css, "§12.1 그림자 금지")

    def test_two_columns_design_34(self):
        self.assertIsNotNone(re.search(r"^##\s*34\.", self.design, re.M), "DESIGN.md 에 §34 가 없다")
        i_open = self.html.index('<div id="mine-body">')
        i_vocab = self.html.index('id="card-vocab"')
        i_night = self.html.index('id="card-night"')
        i_close = self.html.index('</div><!-- /mine-body §34 -->')
        i_coming = self.html.index('id="zone-coming"')
        self.assertTrue(i_open < i_vocab < i_night < i_close < i_coming, "§34 mine-body 는 단어~밤 여섯 카드만 감싼다")
        # §49: mine-body 는 폰 화면처럼 display: contents — 넓은 창의 안쪽 격자(§34)는 없앴다
        self.assertIn("#main.phone-pages > #sidebar > #mine-body { display: contents !important; }", self.css)
        self.assertNotIn('#main.view-day > #sidebar > #mine-body { grid-area: mine;', self.css)

    def test_drawer_design_35(self):
        self.assertIsNotNone(re.search(r"^##\s*35\.", self.design, re.M), "DESIGN.md 에 §35(서랍) 가 없다")
        i_night = self.html.index('id="card-night"')
        i_drawer = self.html.index('id="card-drawer"')
        i_close = self.html.index('</div><!-- /mine-body §34 -->')
        self.assertTrue(i_night < i_drawer < i_close, "§35 서랍은 내 것 안, 밤 일정 다음")
        for i in ("btn-drawer-add", "drawer-add", "drawer-label", "drawer-value", "btn-drawer-save", "btn-drawer-file", "drawer-file", "btn-drawer-cancel", "drawer-list"):
            self.assertIn('id="%s"' % i, self.html, "§35 id=%s 가 없다 — app.js 가 getElementById 로 잡는다" % i)
        self.assertNotIn("#main.view-day #card-drawer { grid-area: drawer; }", self.css, "§49 넓은 창 격자 영역은 없앴다")
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
        self.assertNotIn('"unknown pause" "devices devices"', self.css, "§49 넓은 창 격자 영역은 없앴다")
        self.assertIn(".is-remote #link-restart { display: none; }", self.css)

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
            ink = "currentColor" if f == "favicon.svg" else "#1c1c1a"      # §45 탭 아이콘은 바탕 없이 한 가지 색(화면 밝기를 따른다)
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
        for page in ("today", "todos", "events", "more", "notes", "night", "drawer", "notices", "tools"):
            self.assertIn("'%s'" % page, js_pages)
        self.assertNotIn("'timeline'", js_pages, "§55 '타임라인' 화면은 없다")
        for sec in ("today", "todos", "events", "notes", "night", "drawer", "notices", "tools", "more"):
            self.assertIn('<a href="#/%s" data-sec="%s">' % (sec, sec), self.html, "§41 오른쪽 세로 바 %s" % sec)
        self.assertNotIn('id="phone-menu"', self.html, "§38 의 왼쪽 메뉴는 없다")
        self.assertNotIn('id="phone-tabs"', self.html, "§41 아래 탭은 오른쪽 세로 바로 대체")
        self.assertNotIn('id="phone-fab"', self.html, "§40 동그란 더하기 단추는 없다")
        self.assertIn("window.addEventListener('hashchange', applyPhonePage)", self.js, "뒤로 가기가 앞 화면으로")
        self.assertIn("replace(/^#\\/?/, '')", self.js, "화면 이름은 #/이름 — id 와 겹쳐 브라우저가 스크롤하지 않게")
        self.assertIn("drawOrbit('day', [{ r: OB.R, band: OB.band, spans: spans,", self.js, "§55 하루 궤도")
        self.assertIn("function renderTasksPhone()", self.js)
        self.assertIn("renderPhoneHero();", self.js)
        m = re.search(r"/\* ── §39\.2 폰.*", self.css, re.S)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("@media screen {\n  :root { --rail-w: 44px; --gut: 16px; }", block, "§49 폰 규칙은 모든 폭에(PC 도 같은 화면)")
        self.assertNotIn("@media (max-width: 559px) {", block)
        self.assertIn(".phone-head, .phone-hero, .phone-sec, .phone-rail, .ph-list, .tile-pv { display: none; }", block, "기본은 숨기고 폰 규칙에서 보인다")
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
        self.assertIn("m.classList.add('pg-home');", self.js, "§55 늘 한 장 — §41 의 따로 떨어진 타임라인 화면은 없다")
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
        self.assertIn('<svg id="orbit-svg" viewBox="0 0 600 600"', self.html, "§55 작은 궤도(240)는 없다 — 모든 폭에서 큰 궤도")
        for fn in ("function todoProgress()", "function orbitArc(r, d0, d1, c, back)", "function drawOrbit(kind, rings)"):
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

    def test_geist_font_design_43(self):
        self.assertIsNotNone(re.search(r"^##\s*43\.", self.design, re.M), "DESIGN.md 에 §43 이 없다")
        for fam, f in (("Geist", "GeistVariable.woff2"), ("Geist Mono", "GeistMonoVariable.woff2")):
            self.assertIn('@font-face { font-family: "%s"; font-weight: 100 900;' % fam, self.css)
            self.assertIn('url("/static/%s")' % f, self.css)
            with io.open(os.path.join(ROOT, "static", f), "rb") as fh:
                self.assertEqual(fh.read(4), b"wOF2", "§43 %s 는 woff2 글꼴 파일" % f)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "Geist-LICENSE.txt")), "§43 OFL 사용권 파일을 글꼴 곁에")
        self.assertIn('--font: "Geist", "Pretendard Variable",', self.css, "§43 라틴은 Geist, 한글은 Pretendard")
        self.assertNotIn("fonts.googleapis", self.css + self.html, "외부 리소스 0 — 글꼴도 static 에서")

    def test_icon_cache_bust_design_44(self):
        self.assertIsNotNone(re.search(r"^##\s*44\.", self.design, re.M), "DESIGN.md 에 §44 가 없다")
        login = _read(os.path.join(common.STATIC_DIR, "login.html"))
        man = _read(os.path.join(common.STATIC_DIR, "manifest.json"))
        for page, name in ((self.html, "index.html"), (login, "login.html")):
            head = page[:page.index("</head>")]
            links = re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)"[^>]*href="([^"]+)"', head)
            self.assertTrue(links, name)
            for href in links:
                self.assertIn("?v=", href, "§44 %s 의 아이콘 링크 %s 에 판 번호가 없다 — 크롬이 옛 아이콘을 계속 쓴다" % (name, href))
        for src in re.findall(r'"src":\s*"([^"]+)"', man):
            self.assertIn("?v=", src, "§44 manifest 아이콘 %s 에 판 번호" % src)
        gen = io.open(os.path.join(ROOT, "logo", "make_orbit.py"), encoding="utf-8").read()
        self.assertIn('os.path.join(ROOT, "app_icon.ico")', gen, "§44 app_icon.ico 도 Orbit 아이콘으로 만든다")

    def test_pc_icon_one_color_design_45(self):
        self.assertIsNotNone(re.search(r"^##\s*45\.", self.design, re.M), "DESIGN.md 에 §45 가 없다")
        fav = _read(os.path.join(common.STATIC_DIR, "favicon.svg"))
        self.assertNotIn("#000000", fav, "§45 탭 아이콘에 검은 바탕이 없다")
        self.assertNotIn('fill="#ffffff"', fav, "§45 탭 아이콘에 흰 원판이 없다")
        self.assertIn("@media (prefers-color-scheme:dark){:root{color:#f2f2ef}}", fav, "§45 어두운 화면에서는 흰 마크")
        self.assertIn(":root{color:#1c1c1a}", fav, "§45 밝은 화면에서는 검은 마크")
        login = _read(os.path.join(common.STATIC_DIR, "login.html"))
        for page in (self.html, login):
            self.assertIn('<link rel="icon" href="/static/horarium.ico?v=orbit3" sizes="32x32">', page, "§45 .ico 는 32x32 — 크롬이 svg 를 고른다")
        self.assertNotIn('rel="icon" type="image/png" sizes="192x192"', self.html, "§45 폰용 검은 바탕 png 는 PC 창 아이콘 후보에서 뺀다")
        gen = io.open(os.path.join(ROOT, "logo", "make_orbit.py"), encoding="utf-8").read()
        self.assertIn('imgs = [render_icon(n, None, PC_SCALE, ink=PC_INK) for n in ico_sizes]', gen, "§45 .ico 는 바탕 없이 검은 마크")
        self.assertIn('os.path.join(here, "orbit-mono.ico")', gen)
        with io.open(os.path.join(ROOT, "app_icon.ico"), "rb") as fh:
            self.assertEqual(fh.read(4), b"\x00\x00\x01\x00", "app_icon.ico 는 ico 파일")

    def test_unified_design_49(self):
        self.assertIsNotNone(re.search(r"^##\s*49\.", self.design, re.M), "DESIGN.md 에 §49 가 없다")
        self.assertNotIn("isPhone()", self.js, "§49 폰·PC 구분 없이 같은 화면 — 폭은 isSplit() 하나")
        self.assertIn("var splitMQ = window.matchMedia ? window.matchMedia('(min-width: 1100px)') : null;", self.js)
        self.assertIn("m.classList.toggle('pg-split', split);", self.js)
        self.assertIn("#main.phone-pages.pg-split > .phone-hero { display: flex; flex-direction: column; position: fixed;", self.css,
                      "왼쪽 무대(§50 · §55 궤도)는 붙박이 — 오른쪽 한 장만 스크롤")
        self.assertIn("#main { display: grid; grid-template-columns: minmax(0px, 1fr); }", self.css, "옛 오른쪽 300px 판은 없다")

    def test_big_orbit_design_50(self):
        """§50 넓은 창은 큰 궤도 — 같은 하루를 두 번(세로 타임라인 + 원) 그리지 않는다. §55 부터 세로 타임라인 자체가 없다."""
        self.assertIsNotNone(re.search(r"^##\s*50\.", self.design, re.M), "DESIGN.md 에 §50 이 없다")
        for gone in ("tl-tabs", "tl-seg", "tl-prev"):
            self.assertNotIn(gone, self.html, "§50 왼쪽 위 띠는 없앴다")
            self.assertNotIn(gone, self.js)
        # 무대: 머리 + 큰 궤도 + 작은 궤도 줄 (모두 붙박이)
        self.assertIn("#main.phone-pages.pg-split > .phone-head { position: fixed;", self.css)
        self.assertIn("#main.phone-pages.pg-split > .phone-hero { display: flex; flex-direction: column; position: fixed;", self.css)
        self.assertIn("width: min(100cqw, 100cqh, 840px);", self.css, "남는 자리에 들어가는 가장 큰 정사각형")
        self.assertIn(".orbit-hov, .ob-strip { display: none; }", self.css, "작은 궤도 줄은 넓은 창에서만")
        for el in ('class="orbit-box"', 'id="ob-cats"', 'id="ob-stats"', 'id="orbit-hov"', 'id="ob-strip"', 'id="obs-seg"', 'id="obs-days"', 'id="obs-prev"'):
            self.assertIn(el, self.html)
        hero = self.html[self.html.index('id="phone-strip"'):self.html.index('<aside id="sidebar">')]
        self.assertIn('id="ob-strip"', hero, "작은 궤도 줄은 궤도와 같은 무대 안")
        # 큰 궤도: 치수 · 고리 안 · 구간에 올리기/누르기
        self.assertIn("var OB = { c: 300, R: 244, R2: 219, band: 22,", self.js)
        self.assertIn("function renderOrbitInside(sm, parts, night)", self.js)
        self.assertIn("sm.by_category", self.js, "고리 안은 분류별 시간")
        self.assertIn("function orbitHover(sel)", self.js)
        hov = self.js[self.js.index("function initOrbitHover()"):self.js.index("// ------------------------------------------------------------ §50 작은 궤도 줄")]
        self.assertIn("openPopover({ kind: 'span', span: sp, date: rg.date }, e);", hov, "구간을 누르면 분류 팝오버")
        # 작은 궤도 줄: /api/range 만 쓴다(새 API 없음), 주는 구간까지 · 월은 요약만
        ls = self.js[self.js.index("function loadStrip(force)"):self.js.index("function miniOrbit(")]
        self.assertIn("'/api/range?start='", ls)
        self.assertIn("(strip.mode === 'week' ? 1 : 0)", ls)
        self.assertIn("if (!isSplit() || !state.date", ls, "좁은 창에서는 받지 않는다")
        self.assertIn("STRIP_STALE_MS", ls)
        self.assertIn("var STRIP_KEY = 'orbit.strip'", self.js)
        self.assertIn("function stripMove(dir)", self.js)
        self.assertIn("if (nd > todayStr()) nd = todayStr();", self.js, "줄의 › 는 오늘까지만")
        self.assertIn("loadStrip(false);                                             // §50", self.js, "하루를 받으면 줄도 맞춘다")

    def test_orbit_timeline_design_55(self):
        """§55 타임라인도 궤도로 — 하루 · 주 · 월이 다 원, 휠로 궤도 확대. 직선 타임라인은 없다."""
        self.assertIsNotNone(re.search(r"^##\s*55\.", self.design, re.M), "DESIGN.md 에 §55 가 없다")
        # 55.1 직선 타임라인 · 타임라인 화면 · 범례 띠 · 시간축 배율이 없다
        for gone in ('id="timeline-wrap"', 'id="week-wrap"', 'id="month-wrap"', 'id="timeline"', 'class="legend-bar"', 'class="zoom-bar"', 'data-sec="timeline"'):
            self.assertNotIn(gone, self.html, "§55 " + gone)
        for gone in ("function renderWeek(", "function renderMonth(", "function drawHourGrid(", "function nightBand(", "function setZoom(", "worklog.pxPerMin",
                     "var GUTTER", "var COL_GAP", "function onTimelinePage(", "stage-tl", "pg-timeline", "OB_SMALL"):
            self.assertNotIn(gone, self.js, "§55 " + gone)
        for gone in ("#timeline-wrap", "stage-tl", "pg-timeline", ".zoom-bar", ".mcell"):
            self.assertNotIn(gone, self.css, "§55 " + gone)
        self.assertIn("$('ph-ctl').hidden = false;", self.js, "§55 머리에 하루 · 주 · 월이 늘 보인다")
        self.assertIn("history.replaceState(null, '', '#/today')", self.js, "옛 #/timeline 주소는 오늘로")
        self.assertIn("function periodTitle()", self.js)
        self.assertIn("#main.phone-pages:not(.view-day) #card-period:not([hidden]) { display: block !important; }", self.css, "주 · 월이면 한 장 맨 위에 기간 판")
        # 55.2 구간 이름은 호를 따라 — 아래 반쪽은 거꾸로
        det = self.js[self.js.index("function ozDetail()"):self.js.index("// ---- 확대 · 옮기기 (§55.5)")]
        self.assertIn("s('textPath'", det)
        self.assertIn("mid > 90 && mid < 270", det, "아래 반쪽은 글자가 뒤집히지 않게 반대 방향")
        self.assertIn("truncateToWidth(spanLabel(sp) || cat.name", det)
        # 55.3 주: 고리 일곱, 바깥이 월 · 아래 일곱 칸
        self.assertIn("var OW = { r0: 254, pitch: 18, band: 13 };", self.js)
        self.assertIn("r: OW.r0 - i * OW.pitch", self.js, "바깥(첫째 날 = 월)에서 안쪽으로")
        self.assertIn("function renderWeekDays(rings)", self.js)
        self.assertIn("setView('day', rg.date)", self.js, "날 칸을 누르면 그 날 하루로")
        self.assertIn("'&spans=' + (view === 'week' ? '1' : 'lite')", self.js, "§55.4 월은 시각 · 분류만")
        # 55.4 월: 달력 칸마다 작은 궤도 + 활동 시간
        mg = self.js[self.js.index("function renderMonthGrid()"):self.js.index("// ---- 기간 요약 카드 (주·월)")]
        self.assertIn("miniOrbit(d, d.date, false, true)", mg, "칸마다 그 날의 궤도(구간 색)")
        self.assertIn("fmtCompact(act)", mg, "고리 안에 활동 시간 — 날별 숫자가 줄지 않는다")
        self.assertIn("$('om-sum').textContent", mg, "달력 위 한 줄 — 기간 숫자")
        self.assertIn("function miniOrbit(d, date, isTodayCell, full)", self.js)
        # 55.5 확대: 휠 · 끌기 · 두 손가락 · 전체 보기, 저장하지 않음
        self.assertIn("var OZ_MAX = 24;", self.js)
        for fn in ("function ozWheel(e)", "function ozDown(e)", "function ozMove(e)", "function ozUp(e)", "function ozReset(instant)", "function ringVisible(r, x0, y0, x1, y1)"):
            self.assertIn(fn, self.js)
        self.assertIn("box.addEventListener('wheel', ozWheel, { passive: false });", self.js)
        self.assertIn("$('oz-all').addEventListener('click'", self.js)
        self.assertIn("if (key !== oz.key) {", self.js, "날짜 · 보기가 바뀌면 전체로")
        self.assertNotIn("orbit.zoom", self.js, "배율은 저장하지 않는다")
        self.assertIn("if (!isSplit() && oz.tk <= 1.001 && (dy > 0 || window.scrollY > 2)) return;", self.js, "한 장에서는 아래로 굴리면 페이지가 내려간다")
        self.assertIn("1 - Math.pow(0.7, dt / 16.7)", self.js, "틀이 느려도 같은 시간에 닿는다")
        self.assertIn("stroke-width: calc(22px * var(--kw));", self.css, "고리 굵기는 √배율")
        self.assertIn(".ob-hr { fill: var(--text-3); font-size: calc(11px * var(--u)); }", self.css, "글자는 화면에서 같은 크기")
        self.assertIn(".orbit-box.zoomed { touch-action: none; cursor: grab; }", self.css)
        self.assertIn("touch-action: pan-y;", self.css, "확대 전에는 손가락이 화면을 내린다")
        # 손가락: 한 번은 가운데에, 같은 구간을 한 번 더 누르면 분류
        hov = self.js[self.js.index("function initOrbitHover()"):self.js.index("// ------------------------------------------------------------ §50 작은 궤도 줄")]
        self.assertIn("oz.ptype !== 'mouse' && !(state.obSel && state.obSel.r === sel.r && state.obSel.i === sel.i)", hov)
        # 궤도 쪽으로만: 휠 · 두 손가락의 중심은 고리 위로, 확대한 동안 고리가 늘 화면 안에
        self.assertIn("p = ozOnRing(ozAt(fr, oz.tk, oz.tcx, oz.tcy));", self.js)
        self.assertIn("p: ozOnRing(ozAt(ozFrac(mid[0], mid[1]), oz.k, oz.cx, oz.cy))", self.js)
        cl = self.js[self.js.index("function ozClamp(k, cx, cy) {"):self.js.index("function ozOnRing(p)")]
        self.assertIn("half = Math.min(oz.W, oz.H) / k / 2 * 0.8", cl)
        self.assertIn("lo = Math.max(0, b[0] - half), hi = b[1] + half", cl, "가운데 빈 곳도 원 바깥도 안 된다")

    def test_glance_list_design_58(self):
        """§58 한눈에 — 펼친 묶음은 오늘 · 다가오는 것 둘, 나머지는 접고, 묵은 일은 하나씩 묻는다."""
        self.assertIsNotNone(re.search(r"^##\s*58\.", self.design, re.M), "DESIGN.md 에 §58 이 없다")
        ph = self.js[self.js.index("function renderTasksPhone()"):self.js.index("// ------------------------------------------------------------ §51 받아 적기")]
        for gone in ("grp('하는 중'", "grp('할 일'", "grp('지남 · 오늘'", "grp('이번 주'", "grp('나중에'"):
            self.assertNotIn(gone, ph, "§58 옛 묶음 " + gone)
        self.assertIn("var STALE_DAYS = 3,", self.js)
        self.assertIn("if (x.doing || (d !== null && d <= 0) || (!x.someday && (!a[1] || todoAge(x) < STALE_DAYS))) nowT.push(a);", ph, "오늘 = 하는 중 · 기한 지남/오늘 · 사흘 안")
        self.assertIn("foldGrp('쌓아 둔 것', backT.map(undRow)", ph, "쌓아 둔 것은 접힌 채 개수만")
        self.assertIn("state[flag] ? [head].concat(rows) : [head]", ph)
        # 묵은 일은 하나씩 묻는다
        self.assertIn("if (ask.length) grp('확인할 것' + (ask.length > 1 ? ' · ' + ask.length : ''), [phStale(ask[0][0])]);", ph)
        self.assertIn("!a[0].someday && snoozed[a[0].id] !== today", ph, "'나중에' · 오늘 미룬 것은 묻지 않는다")
        st = self.js[self.js.index("function phStale(x)"):self.js.index("function foldToefl(")]
        for w in ("'끝냈어요'", "upd({ done: 1, day: sideDate() })", "'오늘 할게요'", "upd({ day: sideDate(), someday: 0 })", "'나중에'", "upd({ someday: 1 })",
                  "'지우기'", "'내일 다시 묻기'", "staleSnooze(x.id)", "'일 됐어요. 끝냈나요, 어떻게 할까요?'"):
            self.assertIn(w, st)
        # 한 줄 = 제목 + 오른쪽 한 마디
        self.assertIn(".ph-row:not(.open):not(.inbox) > .ph-b > .ph-m { display: none; }", self.css, "둘째 줄은 펼쳤을 때만")
        self.assertIn("return n === 1 ? '내일' : (n <= 7 ? WEEK[parseDate(day).getDay()] : mdStr(day)) + ' · D-' + n;", self.js)
        self.assertIn(": old >= STALE_DAYS ? [mt, '', old + '일 전'] : [mt, ''];", self.js)
        self.assertIn("text: '오늘 하기'", self.js, "쌓아 둔 것 · 내일 것을 오늘로")
        # 토플 숙제 네 줄 → 한 줄
        self.assertIn("TOEFL_RE = /^토플 (WRT|SPK|RC|LC) 숙제/", self.js)
        self.assertIn("'토플 숙제 ' + tf.length + '과목'", self.js)
        self.assertIn("if (tf.length < 2) return out;", self.js, "하나뿐이면 그대로")

    def test_answer_in_place_design_57(self):
        """§57 비서가 물으면 그 줄 아래에서 답한다 — 답은 받아 적기로, 물은 줄은 닫는다."""
        self.assertIsNotNone(re.search(r"^##\s*57\.", self.design, re.M), "DESIGN.md 에 §57 이 없다")
        ph = self.js[self.js.index("function phInbox(x)"):self.js.index("function growTodoInput()")]
        self.assertIn("if (x.reply && /[?？]\\s*$/.test(x.reply.trim())) b.appendChild(inboxAnswer(x));", ph)
        ia = self.js[self.js.index("function inboxAnswer(x)"):self.js.index("function growTodoInput()")]
        self.assertIn("api('/api/inbox', { text: v, day: sideDate() })", ia, "답은 받아 적기로 — 비서가 물은 말과 함께 읽는다")
        self.assertIn("api('/api/inbox/dismiss', { id: x.id })", ia, "물은 줄은 닫는다")
        self.assertIn("state.ibAns[x.id]", ia, "다시 그려져도 적던 답은 남는다")
        self.assertIn("!e.isComposing", ia, "한글 조합 중 Enter 는 보내지 않는다")
        self.assertIn(".ib-ans-row { display: flex;", self.css)
        # 57.5 '내일' 을 바로 고른다
        for w in ('data-when="today" aria-pressed="true">오늘', 'data-when="tmr" aria-pressed="false">내일', 'data-when="date" id="tw-date-label"', 'type="date" id="tw-date"'):
            self.assertIn(w, self.html, "§57.5 오늘 · 내일 · 날짜")
        self.assertNotIn("btn-todo-tmr", self.html, "단추 하나는 셋으로 바뀌었다")
        self.assertIn("day: whenDay() || sideDate()", self.js)
        self.assertIn("return v && v.set === t && v.day > t ? v.day : null;", self.js, "고른 것은 그 날 동안만 · 앞날만")
        self.assertIn("ahead.filter(function (x) { return dueDays(x.day) <= 7; }).forEach(function (x) { soon.push({ k: keyOf(x.day), li: phTodo(x, false, true) }); });", self.js,
                      "§58 내일 · 고른 날로 적은 것은 '다가오는 것' 에 날짜순")
        self.assertIn("if ((carried || ahead) && !it.done) body.day = sideDate();", self.js)
        self.assertIn("#card-todos .todo-when button.on {", self.css)

    def test_one_off_events_in_list_56(self):
        """§56 한 번뿐인 일정(예비군 같은 것)은 이레 안이면 할 일 목록에도 — 매주 일정은 일정 칸에만."""
        self.assertIsNotNone(re.search(r"^##\s*56\.", self.design, re.M), "DESIGN.md 에 §56 이 없다")
        lp = self.js[self.js.index("function listPlans()"):self.js.index("function renderTasksPhone()")]
        self.assertIn("var oneOff = function (x) { return x.kind === 'event' && !x.repeat && !x.done && x.occ && x.d >= 0 && x.d <= 7; };", lp)
        self.assertIn("return x.kind !== 'event' || oneOff(x);", lp)
        self.assertEqual(self.js.count("var plans = listPlans();"), 2, "폰 목록 · 옛 넓은 목록이 같은 규칙")
        self.assertNotIn("filter(function (x) { return x.kind !== 'event'; })", self.js)

    def test_inbox_design_51(self):
        """§51 받아 적기 · 개인 비서 — 내가 적은 할 일이 맨 위, 적은 글은 claude 가 나누고 결과를 되돌릴 수 있다."""
        self.assertIsNotNone(re.search(r"^##\s*51\.", self.design, re.M), "DESIGN.md 에 §51 이 없다")
        ph = self.js[self.js.index("function renderTasksPhone()"):self.js.index("// ------------------------------------------------------------ §51 받아 적기")]
        order = ["grp('방금 적은 것'", "grp('확인할 것'", "grp('오늘'", "grp('다가오는 것'", "foldGrp('쌓아 둔 것'", "foldGrp('끝낸 것'"]
        pos = [ph.index(g) for g in order]
        self.assertEqual(pos, sorted(pos), "할 일 칸 묶음 순서(§58): 방금 적은 것 → 확인할 것 → 오늘 → 다가오는 것 → 쌓아 둔 것 → 끝낸 것")
        self.assertNotIn("grp('날짜 없음'", ph)
        add = self.js[self.js.index("function addTask()"):self.js.index("function addTodo()")]
        self.assertIn("api('/api/inbox', { text: text, day: whenDay() || sideDate() })", add, "모든 글은 받아 적기로(§57.5 내일 · 고른 날이면 그 날 것)")
        self.assertNotIn("/api/todos'", add)
        self.assertIn("if (e.isComposing || e.keyCode === 229) return;", self.js, "한글 조합 중 Enter 로 넣지 않는다")
        self.assertIn("if (e.key !== 'Enter' || e.shiftKey) return;", self.js, "Shift+Enter 는 줄바꿈")
        self.assertIn("function loadInbox()", self.js)
        self.assertIn("if (busy) state.inboxPoll = setTimeout(loadInbox, 2500);", self.js, "나누는 동안만 다시 묻는다")
        self.assertIn("api('/api/inbox/undo', { id: x.id })", self.js)
        self.assertIn("loadInbox(); loadKeeps();          // §51", self.js, "옆 판을 다시 받을 때 같이")
        # 기억 칸 · 세로 바 · 타일
        self.assertIn('<section class="side-sec" id="card-keeps">', self.html)
        self.assertLess(self.html.index('id="card-todos"'), self.html.index('id="card-keeps"'))
        self.assertIn('<a href="#/keeps" data-sec="keeps">기억</a>', self.html)
        self.assertIn("var TILE_IDS = ['card-keeps',", self.js)
        self.assertIn("keeps: 'card-keeps'", self.js)
        self.assertIn("#main.phone-pages.pg-home #card-keeps", self.css)
        # 서버
        for route in ('("GET", "/api/inbox")', '("POST", "/api/inbox")', '("POST", "/api/inbox/undo")', '("POST", "/api/inbox/dismiss")',
                      '("POST", "/api/inbox/move")', '("GET", "/api/keeps")', '("POST", "/api/keeps/delete")'):
            self.assertIn(route, self.server)
        for table in ("CREATE TABLE IF NOT EXISTS inbox(", "CREATE TABLE IF NOT EXISTS keeps(", "CREATE TABLE IF NOT EXISTS item_meta("):
            self.assertIn(table, self.common)
        self.assertIn("def run_claude(prompt, timeout", self.plans, "claude 부르기는 plans 와 같은 것(창 없이)")

    def test_secretary_design_52(self):
        """§52 비서답게 — 이미 있는 줄을 고치고(기한 포함), 비서의 말을 보이고, 확인할 때까지 남긴다."""
        self.assertIsNotNone(re.search(r"^##\s*52\.", self.design, re.M), "DESIGN.md 에 §52 가 없다")
        self.assertIn("CREATE TABLE IF NOT EXISTS todo_due(", self.common)
        self.assertIn("if (x.reply) b.appendChild(h('div', { class: 'ib-reply', text: x.reply }));", self.js, "비서의 말")
        self.assertIn(".ib-reply {", self.css)
        self.assertIn("text: '기한 빼기'", self.js)
        self.assertIn("api('/api/todos/update', { id: it.id, due: null })", self.js)
        ph = self.js[self.js.index("function renderTasksPhone()"):self.js.index("// ------------------------------------------------------------ §51 받아 적기")]
        self.assertIn(".sort(byDue)", ph, "묶음 안에서 기한 가까운 것이 먼저")
        self.assertIn("meta[1] === 'doing' || meta[3]", self.js, "기한 색을 쓰는 줄도 하는 중 표시는 남는다")
        self.assertIn('TYPES = ("task", "deadline", "event", "note", "done", "same", "update")', self.inbox)
        self.assertIn("SHOW_SEC = 24 * 3600", self.inbox)

    def test_drawer_drag_54(self):
        """§54 서랍은 끌어서 바로 — 글은 글로, 이 PC 의 파일은 서버가 진짜 파일로, 다른 기기는 DownloadURL."""
        self.assertIsNotNone(re.search(r"^##\s*54\.", self.design, re.M), "DESIGN.md 에 §54 가 없다")
        self.assertIn("dt.setData('text/plain', it.value)", self.js, "글 줄은 글로 끈다")
        self.assertIn("dt.setData('DownloadURL'", self.js, "다른 기기: 탐색기 · 바탕화면에 놓으면 내려받기")
        self.assertIn("api('/api/drawer/drag', { id: it.id })", self.js, "이 PC: 서버가 진짜 파일로 끌기")
        dd = self.js[self.js.index("function drawerDrag("):self.js.index("function blockFileDrop(")]
        self.assertIn("e.preventDefault();", dd, "이 PC 에서는 크롬의 끌기를 접는다")
        self.assertIn("document.addEventListener('drop', blockFileDrop);", self.js, "끌던 파일을 Orbit 에 놓아도 화면이 안 바뀐다")
        self.assertNotIn("text: '탐색기'", self.js, "탐색기 단추는 뺐다")
        self.assertIn('("POST", "/api/drawer/drag"): api_drawer_drag', self.server)
        self.assertIn("p === '/api/drawer/drag'", self.js, "목업")
        self.assertIn(u"눌러서 복사 · 끌어서 놓기", self.html)

    def test_open_page_follows_new_build_53(self):
        """§53 옛 화면이 열려 있으면 적은 글이 옛 길로 간다 — 서버가 판을 알려 주고 화면은 쉬는 때에 새로고침."""
        self.assertIsNotNone(re.search(r"^##\s*53\.", self.design, re.M), "DESIGN.md 에 §53 이 없다")
        import shutil, tempfile, time
        import server
        tmp = tempfile.mkdtemp()
        old = common.STATIC_DIR
        try:
            for n in server._BUILD_FILES:
                io.open(os.path.join(tmp, n), "w", encoding="utf-8").write(u"x")
            common.STATIC_DIR = tmp
            a = server._build_id()
            self.assertEqual(a, server._build_id(), "그대로면 같은 판")
            p = os.path.join(tmp, "app.js")
            os.utime(p, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(a, server._build_id(), "화면 파일을 고치면 판이 바뀐다")
        finally:
            common.STATIC_DIR = old
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn('st["build"] = _build_id()', io.open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())
        self.assertIn("checkBuild(st.build);", self.js)
        rw = self.js[self.js.index("function reloadWhenIdle()"):self.js.index("function loadStatus()")]
        self.assertIn("inp.value.trim()) return;", rw, "적던 글이 있으면 새로고침하지 않는다")
        self.assertIn("notes[k].timer", rw, "메모 저장 전이면 새로고침하지 않는다")
        self.assertIn("location.reload();", rw)
        self.assertIn("if (!b || MOCK) return;", self.js)

    def test_design_19_documented(self):
        self.assertIsNotNone(
            re.search(r"^##\s*19\.", self.design, re.M),
            "DESIGN.md 에 §19(밤 일정) 가 없다 — DESIGN.md:4 '바꿔야 하면 이 문서를 먼저 고친다'")
        sec = self.design[self.design.index("## 19."):]
        self.assertIn("### 19.11 검증", sec, "§19 에 검증 절차가 없다")


if __name__ == "__main__":
    unittest.main()
