# -*- coding: utf-8 -*-
"""
tests/test_notices.py — DESIGN §16 학교 공지 수집.

네트워크 없이 돈다: 목록 파싱은 실제 응답을 저장한 고정 HTML(tests/fixtures/grad_list.html,
me_list.html), 수집(sync)은 가짜 fetch 를 주입한다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import io
import os
import sys
import copy
import json
import time
import shutil
import datetime
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402
import notices  # noqa: E402
import server  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
GRAD_URL = "https://www.hongik.ac.kr/kr/education/notice-grad.do"
ME_URL = "https://me.hongik.ac.kr/me/0701.do"


def fixture(name):
    with io.open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def default_cfg():
    """notices.default.json 을 정규화한 실제 기본 설정(규칙 검사용)."""
    with io.open(os.path.join(ROOT, "notices.default.json"), encoding="utf-8") as f:
        return common.normalize_notices_config(json.load(f))


# ============================================================ parse_list
class ParseListTest(unittest.TestCase):
    def setUp(self):
        self.grad = notices.parse_list(fixture("grad_list.html"), GRAD_URL)
        self.me = notices.parse_list(fixture("me_list.html"), ME_URL)

    def test_row_counts(self):
        """tbody 안 20행만 — 배너/네비게이션의 articleNo 링크(대학원 10개)는 잡지 않는다."""
        self.assertEqual(len(self.grad), 20)
        self.assertEqual(len(self.me), 20)

    def test_no_banner_links(self):
        raw = fixture("grad_list.html")
        self.assertIn("subsidiary.do?mode=view&articleNo=93082", raw)   # 배너가 실제로 들어 있고
        for r in self.grad:                                              # 결과에는 없어야 한다
            self.assertNotIn("subsidiary.do", r["url"])
            self.assertNotIn("open-education.do", r["url"])
        self.assertNotIn("93082", [r["article_no"] for r in self.grad])

    def test_grad_first_row_fields(self):
        r = self.grad[0]
        self.assertEqual(r["article_no"], "156857")
        self.assertEqual(r["title"], u"2026 Fall New Students Orientation Article")
        self.assertEqual(r["date"], "2026-09-10")
        self.assertEqual(r["cate"], u"International Students/留学生")
        self.assertTrue(r["is_new"])
        self.assertFalse(r["has_file"])
        self.assertEqual(r["url"], GRAD_URL + "?mode=view&articleNo=156857"
                                              "&article.offset=0&articleLimit=20&noCat=742")

    def test_grad_categories(self):
        cates = set(r["cate"] for r in self.grad)
        for want in (u"장학", u"졸업", u"학적", u"입시"):
            self.assertIn(want, cates)
        self.assertEqual([r["article_no"] for r in self.grad if r["cate"] == u"장학"],
                         ["155990", "155653", "155161"])

    def test_me_has_no_category(self):
        self.assertEqual(set(r["cate"] for r in self.me), set([""]))

    def test_me_amp_escape_and_absolute_url(self):
        """기계과 href 는 &amp; 로 이스케이프돼 있고 상대 경로다 → unescape + urljoin."""
        raw = fixture("me_list.html")
        self.assertIn("?mode=view&amp;articleNo=156497", raw)
        r = self.me[0]
        self.assertEqual(r["article_no"], "156497")
        self.assertEqual(r["url"], ME_URL + "?mode=view&articleNo=156497&article.offset=0&articleLimit=20")
        self.assertNotIn("&amp;", r["url"])

    def test_new_and_file_flags(self):
        self.assertEqual([r["article_no"] for r in self.grad if r["is_new"]],
                         ["156857", "156852", "156643", "156135"])
        by_no = dict((r["article_no"], r) for r in self.grad)
        self.assertTrue(by_no["156852"]["has_file"])
        self.assertFalse(by_no["156857"]["has_file"])
        self.assertTrue(self.me[0]["has_file"])

    def test_title_cleaned(self):
        """제로폭 문자·엔티티가 제목에 남지 않는다(대학원 154991 에 U+200B 가 들어 있다)."""
        t = dict((r["article_no"], r["title"]) for r in self.grad)["154991"]
        self.assertNotIn(u"​", t)
        self.assertTrue(t.endswith(u"제출 안내(학·석사 연계과정 포함)"))

    def test_empty_and_garbage_html(self):
        self.assertEqual(notices.parse_list("", GRAD_URL), [])
        self.assertEqual(notices.parse_list("<html><body>없음</body></html>", GRAD_URL), [])


# ============================================================ parse_deadline
class ParseDeadlineTest(unittest.TestCase):
    def test_six_formats(self):
        cases = [
            (u"대학원 교학팀 내국인 입시 단기 근로학생 모집(~9.20)", "2026-09-10", "2026-09-20"),
            (u"[외부장학] 재단 장학생 모집(~4.19)", "2026-04-01", "2026-04-19"),
            (u"2026학년도 2학기 2차 과정변경 신청 안내(~9/11(금) 11시까지)", "2026-08-19", "2026-09-11"),
            (u"학생 포상 신청 안내(4.6.(월)~4.10.(금))", "2026-03-30", "2026-04-10"),
            (u"대학원 장학금 2차 신청 안내 ~9.30.(수)", "2026-09-01", "2026-09-30"),
            (u"봉사장학생 선발 (8.4.(화)~20.(목))", "2026-07-30", "2026-08-20"),
        ]
        for title, posted, want in cases:
            self.assertEqual(notices.parse_deadline(title, posted), want, title)

    def test_last_date_wins(self):
        self.assertEqual(
            notices.parse_deadline(u"분납 등록금 납부 안내 (9.11~9.16) (대학원)", "2026-08-27"), "2026-09-16")
        self.assertEqual(
            notices.parse_deadline(u"논문지도교수 신청(변경) 안내(9.1.(화)~9.11.(금) *추가기간 없음)",
                                   "2026-08-24"), "2026-09-11")

    def test_hour_is_not_a_date(self):
        """'11시까지' 의 11 을 월/일로 오인하지 않는다."""
        self.assertEqual(notices.parse_deadline(u"과정변경 신청(~7/1(수) 11시까지)", "2026-06-01"), "2026-07-01")
        self.assertIsNone(notices.parse_deadline(u"신청 마감 11시까지", "2026-06-01"))

    def test_year_rollover(self):
        self.assertEqual(notices.parse_deadline(u"겨울 계절학기 신청 (~1.15)", "2026-12-20"), "2027-01-15")
        self.assertEqual(notices.parse_deadline(u"신청 (~12.30)", "2026-12-01"), "2026-12-30")
        # 월 차이가 1이면 같은 해 (12월 게시 → 11.30 은 지난 것으로 둔다)
        self.assertEqual(notices.parse_deadline(u"신청 (~11.30)", "2026-12-05"), "2026-11-30")

    def test_none_cases(self):
        self.assertIsNone(notices.parse_deadline(u"2026학년도 2학기 연구윤리교육 안내", "2026-08-10"))
        self.assertIsNone(notices.parse_deadline(u"", "2026-08-10"))
        self.assertIsNone(notices.parse_deadline(u"신청 (~9.20)", ""))
        self.assertIsNone(notices.parse_deadline(u"신청 (~9.20)", "이상한날짜"))
        self.assertIsNone(notices.parse_deadline(u"신청 (~2.30)", "2026-02-01"))   # 없는 날짜
        self.assertIsNone(notices.parse_deadline(u"[외부장학] 3.1문화재단 장학생 모집", "2026-09-01"))

    def test_full_date_with_year(self):
        self.assertEqual(notices.parse_deadline(u"신청 기간 2026.9.1.~2026.9.11.", "2026-08-24"), "2026-09-11")


# ============================================================ classify
class ClassifyTest(unittest.TestCase):
    def setUp(self):
        cfg = default_cfg()
        self.rules = cfg["rules"]
        self.ct = cfg["category_tier"]

    def cl(self, title, cate=""):
        return notices.classify(title, cate, self.rules, self.ct)

    def test_hide_beats_scholarship(self):
        """留学生 hide 규칙이 '장학' 규칙보다 위 → 외국인 대상 장학 공지는 숨김."""
        tier, _ = self.cl(u"International Students Scholarship 장학 안내")
        self.assertEqual(tier, "hide")
        tier, _ = self.cl(u"[대학혁신지원사업] Global Graduate School Support",
                          u"International Students/留学生")
        self.assertEqual(tier, "hide")

    def test_category_beats_rules(self):
        """카테고리 '장학' 이면 제목에 '장학' 이 없어도 top."""
        tier, tag = self.cl(u"2026학년도 2학기 사이버강좌 운영보조 모집", u"장학")
        self.assertEqual(tier, "top")
        self.assertEqual(tag, u"장학")
        # 카테고리가 규칙(info)보다 먼저 — 제목이 '등록금' 이어도 top
        tier, _ = self.cl(u"등록금 관련 장학 안내", u"장학")
        self.assertEqual(tier, "top")

    def test_research_tag_first(self):
        self.assertEqual(self.cl(u"26-전기 학생포상대상자 안내"), ("top", u"연구·실적"))
        self.assertEqual(self.cl(u"한국장학재단 석사우수장학생(이공계) 선발"), ("top", u"연구·실적"))
        self.assertEqual(self.cl(u"대통령과학장학금 신청 안내"), ("top", u"연구·실적"))

    def test_scholarship_and_work_tags(self):
        self.assertEqual(self.cl(u"2026학년도 2학기 대학원 장학금 2차 신청 안내"), ("top", u"장학"))
        self.assertEqual(self.cl(u"[조교 채용] 행정조교 모집"), ("top", u"근로·조교"))

    def test_info_tiers(self):
        self.assertEqual(self.cl(u"2026-2학기 분납 등록금 납부 안내"), ("info", u"등록"))
        self.assertEqual(self.cl(u"2026년 8월 학위수여식 진행 안내"), ("info", u"학위"))
        self.assertEqual(self.cl(u"2026학년도 2학기 휴학 신청 안내"), ("info", u"학사"))

    def test_paid_opportunities_reach_top(self):
        """수집 검수(2026-09-11)에서 드러난 보강 규칙 — 돈·실적이 걸린 모집은 top 이다."""
        self.assertEqual(self.cl(u"2026-2학기 학습튜터링 튜터 모집"), ("top", u"근로·조교"))
        self.assertEqual(self.cl(u"대학원 교학팀 내국인 입시 봉사학생 모집(~8.13)"), ("top", u"근로·조교"))
        self.assertEqual(self.cl(u"한국공학한림원 차세대공학리더(YEHS) 참여 학생 모집 안내"),
                         ("top", u"연구·실적"))
        self.assertEqual(self.cl(u"대학원생 학술대회 발표 지원 안내"), ("top", u"연구·실적"))

    def test_masters_coursework_is_info_not_other(self):
        """석사 졸업요건·돈 관련 공지는 other 로 묻히지 않고 info 로 남는다."""
        self.assertEqual(self.cl(u"2026학년도 2학기 종합시험 안내"), ("info", u"학위"))
        self.assertEqual(self.cl(u"2026학년도 2학기 외국어시험 안내"), ("info", u"학위"))
        self.assertEqual(self.cl(u"석사학위청구방법 신청(변경) 안내"), ("info", u"학위"))
        self.assertEqual(self.cl(u"2026학년도 2학기 학자금 대출 신청 안내"), ("info", u"등록"))

    def test_undergrad_events_stay_other(self):
        """보강한 규칙이 학부 행사까지 끌어올리지는 않는다."""
        for t in (u"26-전기 아우모비오 원데이 멘토링", u"26-전기 국제사회봉사단 모집",
                  u"26-전기 청년취업사관학교 모집", u"26-전기 예비군훈련 참가 안내"):
            self.assertEqual(self.cl(t)[0], "other", t)

    def test_foreign_tutor_notice_still_hidden(self):
        """튜터 규칙을 더해도 외국인 대상 1:1 Tutor/Tutee 는 hide 가 먼저다."""
        tier, _ = self.cl(u"[대학혁신지원사업] 2026-2 Global Graduate School Academic Support (1:1 Tutor/Tutee)",
                          u"International Students/留学生")
        self.assertEqual(tier, "hide")

    def test_other_default(self):
        self.assertEqual(self.cl(u"연구윤리교육 안내"), ("other", ""))

    def test_regex_error_is_not_fatal(self):
        rules = [{"tier": "top", "pattern": "(", "is_regex": True, "tag": "x"},
                 {"tier": "top", "pattern": u"장학", "is_regex": False, "tag": u"장학"}]
        self.assertEqual(notices.classify(u"장학금 안내", "", rules, {}), ("top", u"장학"))

    def test_fixtures_distribution(self):
        """고정 HTML 전체를 분류했을 때 장학 카테고리 3건이 모두 top 으로 들어간다."""
        rows = notices.parse_list(fixture("grad_list.html"), GRAD_URL)
        tiers = {}
        for r in rows:
            tier, _ = notices.classify(r["title"], r["cate"], self.rules, self.ct)
            tiers.setdefault(tier, []).append(r)
        self.assertEqual(len(tiers.get("hide", [])), 2)           # International Students 2건
        top_nos = set(r["article_no"] for r in tiers.get("top", []))
        self.assertTrue(set(["155990", "155653", "155161"]) <= top_nos)


# ============================================================ fetch_board (가짜 opener)
class _FakeResp(object):
    def __init__(self, text):
        self._data = text.encode("utf-8")

        class H(object):
            @staticmethod
            def get_content_charset():
                return "utf-8"
        self.headers = H()

    def read(self):
        return self._data

    def close(self):
        pass


class FetchBoardTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"pages_per_board": 2, "timeout_sec": 5, "sleep_sec": 0}
        self.board = {"id": "grad", "name": u"대학원", "list_url": GRAD_URL, "enabled": True}

    def test_two_pages_urls_and_ua(self):
        seen = []

        def opener(req, timeout=None):
            seen.append((req.full_url, req.get_header("User-agent")))
            return _FakeResp(fixture("grad_list.html"))

        rows = notices.fetch_board(self.board, self.cfg, opener=opener)
        self.assertEqual(len(seen), 2)
        self.assertIn("article.offset=0", seen[0][0])
        self.assertIn("article.offset=20", seen[1][0])
        self.assertIn("mode=list", seen[0][0])
        self.assertTrue(seen[0][1])                      # UA 헤더 필수
        self.assertEqual(len(rows), 20)                  # 같은 페이지 2번 → article_no 중복 제거

    def test_first_page_failure_raises(self):
        def opener(req, timeout=None):
            raise IOError("no network")
        self.assertRaises(IOError, notices.fetch_board, self.board, self.cfg, opener)

    def test_later_page_failure_keeps_first_page(self):
        state = {"n": 0}

        def opener(req, timeout=None):
            state["n"] += 1
            if state["n"] > 1:
                raise IOError("timeout")
            return _FakeResp(fixture("me_list.html"))

        rows = notices.fetch_board(self.board, self.cfg, opener=opener)
        self.assertEqual(len(rows), 20)


# ============================================================ sync
def make_rows(n, start=1000, posted="2026-09-10", prefix=u"장학금 안내 "):
    return [{"article_no": str(start + i), "title": prefix + str(i), "date": posted,
             "cate": u"장학", "url": "http://x/%d" % (start + i), "is_new": False, "has_file": False}
            for i in range(n)]


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_notice_")
        self.db = os.path.join(self.tmp, "t.db")
        self.conn = common.connect(common.DEFAULT_CONFIG, self.db)
        self.cfg = default_cfg()
        self.cfg["boards"] = [{"id": "grad", "name": u"대학원", "list_url": GRAD_URL, "enabled": True}]
        self.now = datetime.datetime(2026, 9, 11, 12, 0).timestamp()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]

    def test_insert_then_no_duplicate_on_rerun(self):
        fetch = lambda b, c: make_rows(3)
        res = notices.sync(self.conn, self.cfg, self.now, fetch=fetch)
        self.assertEqual((res["new"], res["updated"], res["errors"]), (3, 0, []))
        self.assertEqual(self.count(), 3)
        res = notices.sync(self.conn, self.cfg, self.now + 60, fetch=fetch)
        self.assertEqual((res["new"], res["updated"]), (0, 0))
        self.assertEqual(self.count(), 3)

    def test_update_keeps_first_seen_and_read_ts(self):
        notices.sync(self.conn, self.cfg, self.now, fetch=lambda b, c: make_rows(1))
        self.conn.execute("UPDATE notices SET read_ts=?", (self.now,))
        self.conn.commit()
        rows = make_rows(1)
        rows[0]["title"] = u"장학금 안내 0 (제목 수정, ~9.25)"
        res = notices.sync(self.conn, self.cfg, self.now + 3600, fetch=lambda b, c: rows)
        self.assertEqual((res["new"], res["updated"]), (0, 1))
        r = self.conn.execute("SELECT * FROM notices").fetchone()
        self.assertEqual(r["first_seen"], self.now)          # 유지
        self.assertEqual(r["read_ts"], self.now)             # 유지
        self.assertIn(u"제목 수정", r["title"])                # 갱신
        self.assertEqual(r["deadline"], "2026-09-25")        # 갱신

    def test_tier_tag_deadline_written(self):
        rows = make_rows(1, prefix=u"학생 포상 신청 안내 (~9.20) ")
        rows[0]["cate"] = ""
        notices.sync(self.conn, self.cfg, self.now, fetch=lambda b, c: rows)
        r = self.conn.execute("SELECT * FROM notices").fetchone()
        self.assertEqual(r["tier"], "top")
        self.assertEqual(r["tag"], u"연구·실적")
        self.assertEqual(r["deadline"], "2026-09-20")
        self.assertEqual(r["board"], "grad")

    def test_keep_days_prunes_old(self):
        self.cfg["keep_days"] = 30
        old = make_rows(2, start=1, posted="2026-01-01")
        new = make_rows(2, start=90, posted="2026-09-10")
        notices.sync(self.conn, self.cfg, self.now, fetch=lambda b, c: old + new)
        self.assertEqual(self.count(), 2)
        left = set(r["article_no"] for r in self.conn.execute("SELECT article_no FROM notices"))
        self.assertEqual(left, set(["90", "91"]))

    def test_old_rows_are_never_inserted(self):
        """keep_days 를 넘긴 글은 넣었다 지우지 않고 아예 넣지 않는다 — 매번 new 가 부풀지 않게."""
        self.cfg["keep_days"] = 30
        fetch = lambda b, c: make_rows(2, start=1, posted="2026-01-01") + make_rows(1, start=90)
        for _ in range(2):
            res = notices.sync(self.conn, self.cfg, self.now, fetch=fetch)
        self.assertEqual((res["new"], res["updated"]), (0, 0))
        self.assertEqual(self.count(), 1)

    def test_network_error_is_captured_not_raised(self):
        def boom(b, c):
            raise IOError(u"연결 실패")
        res = notices.sync(self.conn, self.cfg, self.now, fetch=boom)
        self.assertEqual(res["new"], 0)
        self.assertEqual(len(res["errors"]), 1)
        self.assertIn(u"대학원", res["errors"][0])
        self.assertIn(u"연결 실패", common.get_state(self.conn, "notices_last_error"))
        self.assertTrue(float(common.get_state(self.conn, "notices_last_fetch")) >= self.now)

    def test_one_board_fails_other_succeeds(self):
        self.cfg["boards"].append({"id": "me", "name": u"기계공학과", "list_url": ME_URL, "enabled": True})

        def fetch(board, c):
            if board["id"] == "me":
                raise IOError("down")
            return make_rows(2)

        res = notices.sync(self.conn, self.cfg, self.now, fetch=fetch)
        self.assertEqual(res["new"], 2)
        self.assertEqual(len(res["errors"]), 1)

    def test_disabled_board_skipped(self):
        self.cfg["boards"][0]["enabled"] = False
        res = notices.sync(self.conn, self.cfg, self.now, fetch=lambda b, c: make_rows(3))
        self.assertEqual((res["new"], self.count()), (0, 0))
        self.assertIsNone(common.get_state(self.conn, "notices_last_error"))

    def test_same_article_no_on_two_boards(self):
        self.cfg["boards"].append({"id": "me", "name": u"기계공학과", "list_url": ME_URL, "enabled": True})
        res = notices.sync(self.conn, self.cfg, self.now, fetch=lambda b, c: make_rows(1))
        self.assertEqual(res["new"], 2)          # UNIQUE(board, article_no) — 게시판이 다르면 별개

    def test_end_to_end_with_fixture_html(self):
        """고정 HTML → fetch_board → sync 까지 한 번에 (네트워크 없음)."""
        self.cfg["boards"].append({"id": "me", "name": u"기계공학과", "list_url": ME_URL, "enabled": True})
        self.cfg["pages_per_board"] = 1
        self.cfg["sleep_sec"] = 0

        def opener_for(name):
            def opener(req, timeout=None):
                return _FakeResp(fixture(name))
            return opener

        def fetch(board, c):
            return notices.fetch_board(board, c,
                                       opener=opener_for("grad_list.html" if board["id"] == "grad"
                                                         else "me_list.html"))

        res = notices.sync(self.conn, self.cfg, self.now, fetch=fetch)
        self.assertEqual(res["new"], 40)
        # 대학원 4건(장학 카테고리 3 + 근로학생 모집 1)
        # + 기계과 5건(장학신청 2 + 학생포상 1 + 학습튜터링 1 + 차세대공학리더 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM notices WHERE tier='top'").fetchone()[0], 9)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM notices WHERE tag='연구·실적'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM notices WHERE tier='hide'").fetchone()[0], 2)


# ============================================================ common.load_notices_config
class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_ncfg_")
        self.old = (common.NOTICES_PATH, common.NOTICES_DEFAULT_PATH)
        common.NOTICES_PATH = os.path.join(self.tmp, "notices.json")

    def tearDown(self):
        common.NOTICES_PATH, common.NOTICES_DEFAULT_PATH = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, obj):
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False))

    def test_copies_default_when_missing(self):
        cfg = common.load_notices_config()
        self.assertTrue(os.path.exists(common.NOTICES_PATH))
        self.assertEqual([b["id"] for b in cfg["boards"]], ["grad", "me"])
        self.assertEqual(cfg["category_tier"], {u"장학": "top"})
        self.assertEqual(cfg["pages_per_board"], 2)

    def test_broken_json_does_not_crash(self):
        with io.open(common.NOTICES_PATH, "w", encoding="utf-8") as f:
            f.write(u"{ 깨진 ")
        cfg = common.load_notices_config()
        self.assertEqual(cfg["boards"], [])
        self.assertEqual(cfg["rules"], [])

    def test_default_version_refreshes_file(self):
        """§15 와 같은 규칙: default 의 version 이 더 크면 .bak 을 남기고 새로 복사한다."""
        common.NOTICES_DEFAULT_PATH = os.path.join(self.tmp, "notices.default.json")
        self.write(common.NOTICES_PATH, {"version": 1, "boards": [], "rules": [
            {"tier": "top", "pattern": u"낡은규칙"}]})
        self.write(common.NOTICES_DEFAULT_PATH, {"version": 2, "boards": [], "rules": [
            {"tier": "top", "pattern": u"새규칙"}]})
        cfg = common.load_notices_config()
        self.assertEqual(cfg["version"], 2)
        self.assertEqual(cfg["rules"][0]["pattern"], u"새규칙")
        self.assertTrue(os.path.exists(common.NOTICES_PATH + ".bak"))

    def test_user_file_wins_and_new_keys_filled(self):
        common.NOTICES_DEFAULT_PATH = os.path.join(self.tmp, "notices.default.json")
        self.write(common.NOTICES_PATH, {"version": 3, "pages_per_board": 5, "boards": [], "rules": []})
        self.write(common.NOTICES_DEFAULT_PATH, {"version": 3, "pages_per_board": 2, "keep_days": 42,
                                                 "boards": [], "rules": []})
        cfg = common.load_notices_config()
        self.assertEqual(cfg["pages_per_board"], 5)   # 사용자 값이 이김
        self.assertEqual(cfg["keep_days"], 42)        # default 에만 있는 키는 채워진다

    def test_normalize_drops_garbage(self):
        cfg = common.normalize_notices_config({
            "pages_per_board": "많이", "timeout_sec": 999, "keep_days": -5,
            "boards": [{"id": "a"}, {"id": "b", "list_url": "http://x"}, "쓰레기",
                       {"id": "b", "list_url": "http://y"}],
            "rules": [{"tier": "없는tier", "pattern": "x"}, {"tier": "top"},
                      {"tier": "info", "pattern": "y"}],
            "category_tier": {u"장학": "top", u"졸업": "없는tier", 1: "top"},
        })
        self.assertEqual(cfg["pages_per_board"], 2)      # 잘못된 값 → 기본값
        self.assertEqual(cfg["timeout_sec"], 120)        # 상한
        self.assertEqual(cfg["keep_days"], 1)            # 하한
        self.assertEqual([b["id"] for b in cfg["boards"]], ["b"])
        self.assertEqual(cfg["boards"][0]["name"], "b")  # name 없으면 id
        self.assertEqual(len(cfg["rules"]), 1)
        self.assertEqual(cfg["category_tier"], {u"장학": "top"})

    def test_schema_has_notices_table(self):
        tmpdb = os.path.join(self.tmp, "s.db")
        conn = common.connect(common.DEFAULT_CONFIG, tmpdb)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(notices)")]
            for c in ("board", "article_no", "title", "url", "posted", "cate", "tier", "tag",
                      "deadline", "first_seen", "read_ts"):
                self.assertIn(c, cols)
        finally:
            conn.close()


# ============================================================ server API (§16.6)
class ServerNoticesApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_napi_")
        self.db = os.path.join(self.tmp, "t.db")
        self._old_env = os.environ.get("MAENG_WORKLOG_DB")
        os.environ["MAENG_WORKLOG_DB"] = self.db
        self._old_cfg = server.CFG
        server.CFG = copy.deepcopy(common.DEFAULT_CONFIG)
        self.conn = common.connect(server.CFG, self.db)
        self.now = datetime.datetime(2026, 9, 11, 12, 0).timestamp()
        self._old_now = common.now_ts
        common.now_ts = lambda: self.now
        self._old_load = common.load_notices_config
        cfg = default_cfg()
        common.load_notices_config = lambda: cfg
        # 백그라운드 수집이 실제 네트워크를 두드리지 않도록 막는다
        self._old_bg = server._notices_sync_bg
        self.bg_calls = []
        server._notices_sync_bg = lambda c: self.bg_calls.append(c) or True

    def tearDown(self):
        server._notices_sync_bg = self._old_bg
        common.load_notices_config = self._old_load
        common.now_ts = self._old_now
        server.CFG = self._old_cfg
        self.conn.close()
        if self._old_env is None:
            os.environ.pop("MAENG_WORKLOG_DB", None)
        else:
            os.environ["MAENG_WORKLOG_DB"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, no, title, tier="top", tag=u"장학", posted="2026-09-10", deadline=None,
            board="grad", read=None):
        self.conn.execute(
            "INSERT INTO notices(board, article_no, title, url, posted, cate, tier, tag, deadline,"
            " first_seen, read_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (board, str(no), title, "http://x/%s" % no, posted, "", tier, tag, deadline,
             self.now, read))
        self.conn.commit()

    def get(self, **q):
        query = dict((k, [str(v)]) for k, v in q.items())
        return server.api_notices(self.conn, query, {})

    def test_empty_db_triggers_background_fetch(self):
        r = self.get()
        self.assertEqual(r["items"], [])
        self.assertEqual(len(self.bg_calls), 1)      # last_fetch 가 없으니 수집 시작
        self.assertIsNone(r["last_fetch"])
        self.assertIsNone(r["error"])

    def test_recent_fetch_does_not_refetch(self):
        common.set_state(self.conn, "notices_last_fetch", self.now - 60)
        self.get()
        self.assertEqual(self.bg_calls, [])

    def test_stale_fetch_refetches(self):
        common.set_state(self.conn, "notices_last_fetch", self.now - 7 * 3600)
        self.get()
        self.assertEqual(len(self.bg_calls), 1)

    def test_tier_filter_and_counts(self):
        self.add(1, u"장학금 신청")
        self.add(2, u"등록금 분납", tier="info", tag=u"등록")
        self.add(3, u"연구윤리교육", tier="other", tag="")
        self.add(4, u"International Students", tier="hide", tag="")
        r = self.get()
        self.assertEqual([i["title"] for i in r["items"]], [u"장학금 신청"])
        self.assertEqual(r["counts"], {"top": 1, "info": 1, "other": 1, "unread": 3, "unread_top": 1})
        r = self.get(tier="all")
        self.assertEqual(len(r["items"]), 3)          # hide 는 all 에서도 제외
        self.assertNotIn(u"International Students", [i["title"] for i in r["items"]])

    def test_board_name_and_d_day_and_is_new(self):
        self.add(1, u"장학금 신청", deadline="2026-09-14")
        self.add(2, u"기계과 장학", board="me", posted="2026-08-01", deadline="2026-09-01")
        items = dict((i["board"], i) for i in self.get()["items"])
        self.assertEqual(items["grad"]["board_name"], u"대학원")
        self.assertEqual(items["me"]["board_name"], u"기계공학과")
        self.assertEqual(items["grad"]["d_day"], 3)
        self.assertEqual(items["me"]["d_day"], -10)
        self.assertTrue(items["grad"]["is_new"])       # 하루 전
        self.assertFalse(items["me"]["is_new"])        # 40일 전
        self.assertFalse(items["grad"]["read"])

    def test_no_deadline_gives_null_d_day(self):
        self.add(1, u"장학금 신청")
        self.assertIsNone(self.get()["items"][0]["d_day"])
        self.assertIsNone(self.get()["items"][0]["deadline"])

    def test_sort_unread_then_deadline_then_posted(self):
        self.add(1, u"읽음-임박", deadline="2026-09-12", read=self.now)
        self.add(2, u"미열람-마감없음-오래됨", posted="2026-08-01")
        self.add(3, u"미열람-임박", deadline="2026-09-13")
        self.add(4, u"미열람-더임박", deadline="2026-09-11")
        self.add(5, u"미열람-지난마감", deadline="2026-09-01")
        self.add(6, u"미열람-마감없음-최근", posted="2026-09-09")
        got = [i["title"] for i in self.get()["items"]]
        self.assertEqual(got, [u"미열람-더임박", u"미열람-임박", u"미열람-마감없음-최근",
                               u"미열람-마감없음-오래됨", u"미열람-지난마감", u"읽음-임박"])

    def test_limit(self):
        for i in range(5):
            self.add(i, u"장학 %d" % i)
        self.assertEqual(len(self.get(limit=2)["items"]), 2)
        self.assertEqual(self.get(limit=2)["total"], 5)
        self.assertEqual(len(self.get(limit=999)["items"]), 5)   # 상한으로 잘림, 오류 아님

    def test_bad_params(self):
        self.assertRaises(server.ApiError, self.get, tier="없는것")
        self.assertRaises(server.ApiError, self.get, limit="많이")

    def test_error_state_surfaces(self):
        common.set_state(self.conn, "notices_last_error", u"대학원: 연결 실패")
        common.set_state(self.conn, "notices_last_fetch", self.now - 60)
        r = self.get()
        self.assertEqual(r["error"], u"대학원: 연결 실패")

    def test_read_one_and_all(self):
        self.add(1, u"장학 A")
        self.add(2, u"장학 B")
        nid = self.conn.execute("SELECT id FROM notices ORDER BY id").fetchone()["id"]
        server.api_notices_read(self.conn, {}, {"id": nid})
        items = dict((i["id"], i) for i in self.get()["items"])
        self.assertTrue(items[nid]["read"])
        self.assertEqual(self.get()["counts"]["unread"], 1)
        server.api_notices_read(self.conn, {}, {"all": True})
        self.assertEqual(self.get()["counts"]["unread"], 0)

    def test_read_missing_id(self):
        self.assertRaises(server.ApiError, server.api_notices_read, self.conn, {}, {"id": 9999})
        self.assertRaises(server.ApiError, server.api_notices_read, self.conn, {}, {})

    def test_refresh_is_synchronous(self):
        calls = []

        def fake_sync(conn, cfg, now):
            calls.append(now)
            conn.execute("INSERT INTO notices(board, article_no, title, url, posted, cate, tier, tag,"
                         " deadline, first_seen, read_ts) VALUES('grad','1','새 장학','u','2026-09-11',"
                         "'','top','장학',NULL,?,NULL)", (now,))
            conn.commit()
            return {"new": 1, "updated": 0, "errors": []}

        old = server._notices_sync_now
        server._notices_sync_now = fake_sync
        try:
            r = server.api_notices_refresh(self.conn, {}, {})
        finally:
            server._notices_sync_now = old
        self.assertEqual(r, {"new": 1, "updated": 0, "errors": []})
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.get()["items"]), 1)

    def test_refresh_reports_errors_without_raising(self):
        old = server._notices_sync_now
        server._notices_sync_now = lambda conn, cfg, now: {"new": 0, "updated": 0,
                                                          "errors": [u"대학원: 연결 실패"]}
        try:
            r = server.api_notices_refresh(self.conn, {}, {})
        finally:
            server._notices_sync_now = old
        self.assertEqual(r["new"], 0)
        self.assertEqual(len(r["errors"]), 1)

    def test_routes_registered(self):
        self.assertIn(("GET", "/api/notices"), server.ROUTES)
        self.assertIn(("POST", "/api/notices/read"), server.ROUTES)
        self.assertIn(("POST", "/api/notices/refresh"), server.ROUTES)

    def test_response_is_json_serializable(self):
        self.add(1, u"장학금 신청", deadline="2026-09-14")
        json.dumps(self.get(), ensure_ascii=False, allow_nan=False)


class BackgroundSyncTest(unittest.TestCase):
    """진행 중이면 재요청을 무시하는지 (전역 락)."""

    def test_second_call_ignored_while_running(self):
        import threading
        started, release = threading.Event(), threading.Event()
        old_sync = notices.sync
        old_connect = common.connect

        def slow_sync(conn, cfg, now=None, fetch=None):
            started.set()
            release.wait(5)
            return {"new": 0, "updated": 0, "errors": []}

        class FakeConn(object):
            def close(self):
                pass

        notices.sync = slow_sync
        common.connect = lambda *a, **k: FakeConn()
        try:
            self.assertTrue(server._notices_sync_bg({"boards": []}))
            self.assertTrue(started.wait(5))
            self.assertFalse(server._notices_sync_bg({"boards": []}))   # 진행 중 → 무시
        finally:
            release.set()
            notices.sync = old_sync
            common.connect = old_connect
        for _ in range(100):
            if not server._notices_busy.locked():
                break
            import time
            time.sleep(0.02)
        self.assertFalse(server._notices_busy.locked())


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ============================================================ §17 상세 본문 파서
class ParseDetailTest(unittest.TestCase):
    """상세 페이지도 정적 HTML — 실제 응답을 저장한 tests/fixtures/grad_view.html 로만 검사(네트워크 없음).
    이 글이 §17 의 기준 사례다: articleNo=155653 '2026학년도 2학기 대학원 장학금 2차 신청 안내'."""

    def setUp(self):
        self.d = notices.parse_detail(fixture("grad_view.html"))

    def test_body_is_extracted_and_reasonable_length(self):
        body = self.d["body"]
        self.assertTrue(1500 < len(body) < 4000, len(body))
        self.assertTrue(body.startswith(u"2026학년도 2학기 대학원 장학금 2차 신청"))

    def test_body_has_no_tags_or_entities(self):
        body = self.d["body"]
        self.assertNotIn("<", body)
        self.assertNotIn("&nbsp;", body)
        self.assertNotIn("&amp;", body)
        self.assertNotIn(u" ", body)

    def test_body_keeps_the_three_facts_that_matter(self):
        """§17 검증 기준 — 이 세 가지가 본문에 남아 있어야 분석이 제대로 나온다."""
        body = self.d["body"]
        self.assertIn(u"면학장학금(일반대학원)", body)
        self.assertIn(u"복학자 불가", body)
        self.assertIn(u"학과장 추천", body)
        self.assertIn(u"연장 신청 장학금 미신청자(이공계우수", body)
        self.assertIn(u"봉사장학금과 강의운영보조장학금은 다른 장학금과 이중수혜가 가능함", body)

    def test_line_breaks_kept(self):
        """<br>·</p> 는 개행으로 — 항목이 한 줄로 붙으면 사람도 모델도 읽기 나쁘다."""
        self.assertGreater(self.d["body"].count("\n"), 10)

    def test_files(self):
        self.assertEqual(self.d["files"], [u"대학원장학금신청서류양식_2차_20262.zip"])

    def test_missing_content_box(self):
        d = notices.parse_detail(u"<html><body><p>없음</p></body></html>")
        self.assertEqual(d["body"], "")
        self.assertEqual(d["files"], [])
        self.assertEqual(notices.parse_detail(None), {"body": "", "files": []})

    def test_file_dedup_and_extensions(self):
        html_text = (u'<div class="b-content-box"><p>본문</p></div></div>'
                     u'<a><span>notice_a.hwp</span></a><a><span>notice_a.hwp</span></a>'
                     u'<a><span>notice_b.PDF</span></a><a><span>notice_c.hwpx</span></a>'
                     u'<a><span>notice_d.txt</span></a>')
        d = notices.parse_detail(html_text)
        # 파일명 생기기 전까지 4자 이상(정규식 {4,60}) — 짧은 이름은 액자 텍스트로 보지 않는다.
        self.assertEqual(d["files"], ["notice_a.hwp", "notice_b.PDF", "notice_c.hwpx"])

    def test_detail_url(self):
        self.assertEqual(
            notices.detail_url(GRAD_URL, "155653"),
            GRAD_URL + "?mode=view&articleNo=155653&article.offset=0&articleLimit=20")
        self.assertIn("&mode=view", notices.detail_url(GRAD_URL + "?x=1", 7))

    def test_fetch_detail_uses_ua_and_url(self):
        seen = []

        def opener(req, timeout=None):
            seen.append((req.full_url, req.get_header("User-agent")))
            return _FakeResp(fixture("grad_view.html"))

        d = notices.fetch_detail(GRAD_URL, "155653", {"timeout_sec": 5}, opener=opener)
        self.assertEqual(len(seen), 1)
        self.assertIn("articleNo=155653", seen[0][0])
        self.assertTrue(seen[0][1])
        self.assertIn(u"면학장학금", d["body"])


# ============================================================ §17 프롬프트·정규화
class PromptTest(unittest.TestCase):
    def test_prompt_carries_profile_body_and_rules(self):
        p = notices.build_prompt(u"장학금 2차 신청", u"본문입니다",
                                 [u"서식.zip"], {u"과정": u"석사 1학기차"},
                                 board_name=u"대학원", posted="2026-08-26")
        self.assertIn(u"- 과정: 석사 1학기차", p)
        self.assertIn(u"장학금 2차 신청", p)
        self.assertIn(u"본문입니다", p)
        self.assertIn(u"서식.zip", p)
        self.assertIn(u"2026-08-26", p)
        self.assertIn(u"지어내지 말 것", p)
        self.assertIn(u"확인필요", p)
        self.assertIn(u"중복수혜", p)          # 사용자가 특히 주의하라고 한 대목
        self.assertIn(u"이중수혜", p)
        self.assertIn(u'"verdict"', p)

    def test_prompt_states_today_and_deadline_rule(self):
        """마감이 지났는지 판단하려면 오늘이 며칠인지 알아야 한다 — CLI 에 기대지 않고 넣는다."""
        p = notices.build_prompt(u"제목", u"본문", None, None,
                                 board_name=u"대학원", posted="2026-08-25",
                                 today="2026-09-11")
        self.assertIn(u"오늘 날짜: 2026-09-11", p)
        self.assertIn(u"신청 기간이 이미 끝났으면", p)

    def test_prompt_today_defaults_to_now(self):
        p = notices.build_prompt(u"제목", u"본문")
        self.assertIn(u"오늘 날짜: " + time.strftime("%Y-%m-%d"), p)

    def test_prompt_falls_back_to_default_profile(self):
        p = notices.build_prompt(u"제목", u"본문", None, {})
        self.assertIn(u"이공계우수장학금", p)

    def test_prompt_truncates_long_body(self):
        p = notices.build_prompt(u"제목", u"가" * (notices.BODY_MAX + 500))
        self.assertLess(len(p), notices.BODY_MAX + 3000)
        self.assertIn(u"본문이 길어 여기서 잘림", p)

    def test_real_config_profile_reaches_prompt(self):
        """notices.default.json 의 profile 이 정규화를 거쳐 프롬프트까지 간다."""
        prof = default_cfg()["profile"]
        self.assertIn(u"현재 장학", prof)
        self.assertIn(u"이공계우수", prof[u"현재 장학"])
        self.assertIn(u"이공계우수", notices.build_prompt(u"t", u"b", None, prof))


class NormalizeAnalysisTest(unittest.TestCase):
    GOOD = {"summary": u"요약", "target": u"석사 재학생", "amount": u"250만원",
            "period": u"8.26~9.11", "how": u"학과 조교", "docs": [u"신청서"],
            "verdict": u"가능", "why": u"본문에 그렇게 적혀 있다",
            "actions": [u"신청서를 낸다"], "caution": u"중복수혜 불가"}

    def test_good(self):
        got = notices.normalize_analysis(dict(self.GOOD))
        self.assertEqual(got["verdict"], u"가능")
        self.assertEqual(got["docs"], [u"신청서"])
        self.assertEqual(sorted(got.keys()), sorted(notices.ANALYSIS_KEYS))

    def test_bad_types(self):
        self.assertIsNone(notices.normalize_analysis(None))
        self.assertIsNone(notices.normalize_analysis("가능"))
        self.assertIsNone(notices.normalize_analysis([]))
        self.assertIsNone(notices.normalize_analysis({}))            # 알맹이 없음

    def test_unknown_verdict_becomes_check(self):
        d = dict(self.GOOD, verdict=u"아마도")
        self.assertEqual(notices.normalize_analysis(d)["verdict"], u"확인필요")
        d = dict(self.GOOD, verdict=u"신청 가능")                     # 안에 들어 있으면 건져낸다
        self.assertEqual(notices.normalize_analysis(d)["verdict"], u"가능")
        d = dict(self.GOOD, verdict=None)
        self.assertEqual(notices.normalize_analysis(d)["verdict"], u"확인필요")

    def test_actions_capped_and_flattened(self):
        d = dict(self.GOOD, actions=[u"a", u"b", u"c", u"d", u"e", u"f"], docs=u"한 줄 서류")
        got = notices.normalize_analysis(d)
        self.assertEqual(len(got["actions"]), notices.ACTIONS_MAX)
        self.assertEqual(got["docs"], [u"한 줄 서류"])

    def test_long_value_is_cut_with_ellipsis(self):
        """갈래가 여럿인 공지의 caution 은 실제로 600자를 넘는다. 잘리더라도 말줄임표로 티가 나야 한다."""
        got = notices.normalize_analysis(dict(self.GOOD, caution=u"주" * 900))
        self.assertEqual(len(got["caution"]), 800)
        self.assertTrue(got["caution"].endswith(u"…"))
        keep = u"주" * 700                                    # 800자 이내면 그대로 둔다
        self.assertEqual(notices.normalize_analysis(dict(self.GOOD, caution=keep))["caution"], keep)

    def test_whitespace_collapsed(self):
        got = notices.normalize_analysis(dict(self.GOOD, summary=u"두\n\n줄  요약"))
        self.assertEqual(got["summary"], u"두 줄 요약")


# ============================================================ §17 저장·조회 흐름
class DetailStoreTest(unittest.TestCase):
    """ask_claude_json 을 가짜로 주입했을 때 수집→분석→저장→조회가 도는지."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_ndetail_")
        self.db = os.path.join(self.tmp, "t.db")
        self.conn = common.connect(common.DEFAULT_CONFIG, self.db)
        self.cfg = default_cfg()
        self.conn.execute(
            "INSERT INTO notices(board, article_no, title, url, posted, cate, tier, tag, deadline,"
            " first_seen, read_ts) VALUES('grad','155653',?,?,'2026-08-26',?, 'top', ?, NULL, 1.0, NULL)",
            (u"2026학년도 2학기 대학원 장학금 2차 신청 안내",
             GRAD_URL + "?mode=view&articleNo=155653", u"장학", u"장학"))
        self.conn.commit()
        self.nid = self.conn.execute("SELECT id FROM notices").fetchone()["id"]

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def opener(self, count=None):
        def _o(req, timeout=None):
            if count is not None:
                count.append(req.full_url)
            return _FakeResp(fixture("grad_view.html"))
        return _o

    def test_schema_table_exists(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(notice_detail)")]
        self.assertEqual(cols, ["notice_id", "body", "files", "fetched_ts",
                                "analysis", "analyzed_ts", "model"])

    def test_ensure_detail_fetches_once_then_reuses(self):
        seen = []
        row = self.conn.execute("SELECT * FROM notices WHERE id=?", (self.nid,)).fetchone()
        d1 = notices.ensure_detail(self.conn, row, self.cfg, opener=self.opener(seen))
        self.assertEqual(len(seen), 1)
        self.assertIn(u"면학장학금", d1["body"])
        self.assertEqual(d1["files"], [u"대학원장학금신청서류양식_2차_20262.zip"])
        d2 = notices.ensure_detail(self.conn, row, self.cfg, opener=self.opener(seen))
        self.assertEqual(len(seen), 1)                    # 같은 글은 한 번만 받는다
        self.assertEqual(d2["body"], d1["body"])

    def test_save_and_load_roundtrip(self):
        notices.save_detail(self.conn, self.nid, u"본문", [u"a.hwp"], now=100.0)
        got = notices.load_detail(self.conn, self.nid)
        self.assertEqual((got["body"], got["files"], got["fetched_ts"]), (u"본문", [u"a.hwp"], 100.0))
        self.assertIsNone(got["analysis"])
        notices.save_analysis(self.conn, self.nid, dict(NormalizeAnalysisTest.GOOD), now=200.0)
        got = notices.load_detail(self.conn, self.nid)
        self.assertEqual(got["analysis"]["verdict"], u"가능")
        self.assertEqual(got["analyzed_ts"], 200.0)
        self.assertEqual(got["model"], notices.CLAUDE_MODEL)
        self.assertEqual(got["body"], u"본문")            # 분석 저장이 본문을 지우지 않는다

    def test_save_detail_keeps_existing_analysis(self):
        notices.save_analysis(self.conn, self.nid, dict(NormalizeAnalysisTest.GOOD))
        notices.save_detail(self.conn, self.nid, u"새 본문", [])
        got = notices.load_detail(self.conn, self.nid)
        self.assertEqual(got["body"], u"새 본문")
        self.assertIsNotNone(got["analysis"])

    def test_load_detail_missing_and_broken_json(self):
        self.assertIsNone(notices.load_detail(self.conn, self.nid))
        self.conn.execute("INSERT INTO notice_detail(notice_id, body, files, analysis) "
                          "VALUES(?, 'b', '{{oops', 'not json')", (self.nid,))
        self.conn.commit()
        got = notices.load_detail(self.conn, self.nid)
        self.assertEqual(got["files"], [])                # 깨진 JSON 은 조용히 빈 값
        self.assertIsNone(got["analysis"])

    def test_analyze_notice_end_to_end_with_fake_claude(self):
        seen = {}

        def fake_ask(prompt, timeout=None):
            seen["prompt"] = prompt
            return dict(NormalizeAnalysisTest.GOOD)

        got = notices.analyze_notice(self.conn, self.nid, self.cfg,
                                     opener=self.opener(), ask=fake_ask, now=300.0)
        self.assertEqual(got["verdict"], u"가능")
        self.assertIn(u"면학장학금", seen["prompt"])          # 본문이 프롬프트에 들어갔다
        self.assertIn(u"이공계우수", seen["prompt"])           # 프로필도
        self.assertIn(u"대학원", seen["prompt"])
        stored = notices.load_detail(self.conn, self.nid)
        self.assertEqual(stored["analysis"], got)
        self.assertEqual(stored["analyzed_ts"], 300.0)

    def test_analyze_notice_survives_bad_answers(self):
        for bad in (None, "그냥 글", {"nope": 1}):
            self.assertIsNone(notices.analyze_notice(
                self.conn, self.nid, self.cfg, opener=self.opener(),
                ask=lambda p, t=None, b=bad: b))
        self.assertIsNone(notices.load_detail(self.conn, self.nid)["analysis"])

    def test_analyze_notice_survives_network_failure(self):
        def boom(req, timeout=None):
            raise IOError("no network")
        self.assertIsNone(notices.analyze_notice(self.conn, self.nid, self.cfg,
                                                 opener=boom, ask=lambda p, t=None: {}))

    def test_analyze_notice_unknown_id(self):
        self.assertIsNone(notices.analyze_notice(self.conn, 99999, self.cfg,
                                                 ask=lambda p, t=None: {}))

    def test_analyze_needs_body(self):
        self.assertIsNone(notices.analyze(u"제목", u"  ", ask=lambda p, t=None: {"summary": "x"}))

    def test_analyze_accepts_one_arg_fake(self):
        got = notices.analyze(u"제목", u"본문", ask=lambda p: dict(NormalizeAnalysisTest.GOOD))
        self.assertEqual(got["verdict"], u"가능")


# ============================================================ §17 claude 없을 때
class ClaudeMissingTest(unittest.TestCase):
    def setUp(self):
        self._old = notices.find_claude
        notices.find_claude = lambda: None

    def tearDown(self):
        notices.find_claude = self._old

    def test_available_false_and_ask_returns_none(self):
        self.assertFalse(notices.analysis_available())
        self.assertIsNone(notices.ask_claude_json(u"아무 프롬프트"))

    def test_analyze_returns_none_without_claude(self):
        self.assertIsNone(notices.analyze(u"제목", u"본문"))

    def test_no_window_shape(self):
        kw = notices._no_window()
        self.assertIsInstance(kw, dict)
        if os.name == "nt":
            self.assertEqual(kw.get("creationflags"), 0x08000000)


# ============================================================ §17 서버 API
class ServerNoticesDetailApiTest(unittest.TestCase):
    """detail / analyze 엔드포인트. 네트워크와 claude 는 둘 다 가짜로 바꿔 둔다.
    ServerNoticesApiTest 를 상속하면 그쪽 테스트가 이 setUp 으로 한 번 더 돌아 버리므로,
    필요한 준비 함수만 unbound 로 빌려 쓴다."""
    add = ServerNoticesApiTest.add
    get = ServerNoticesApiTest.get

    def setUp(self):
        ServerNoticesApiTest.setUp(self)
        self.add(155653, u"2026학년도 2학기 대학원 장학금 2차 신청 안내")
        self.nid = self.conn.execute("SELECT id FROM notices").fetchone()["id"]
        self.fetched = []
        self._old_ensure = notices.ensure_detail

        def fake_ensure(conn, row, cfg, opener=None, boards=None):
            self.fetched.append(row["id"])
            d = notices.parse_detail(fixture("grad_view.html"))
            notices.save_detail(conn, row["id"], d["body"], d["files"])
            return notices.load_detail(conn, row["id"])
        notices.ensure_detail = fake_ensure
        self._old_avail = notices.analysis_available
        notices.analysis_available = lambda: True
        self._old_bg_an = server._notices_analyze_bg
        self.analyze_calls = []
        server._notices_analyze_bg = lambda nid, cfg: self.analyze_calls.append((nid, cfg))

    def tearDown(self):
        server._notices_analyze_bg = self._old_bg_an
        notices.analysis_available = self._old_avail
        notices.ensure_detail = self._old_ensure
        with server._notices_analyzing_lock:
            server._notices_analyzing.clear()
        ServerNoticesApiTest.tearDown(self)

    def detail(self, nid=None):
        return server.api_notices_detail(self.conn, {"id": [str(nid or self.nid)]}, {})

    def test_detail_fetches_body_once(self):
        r = self.detail()
        self.assertEqual(self.fetched, [self.nid])
        self.assertIn(u"면학장학금", r["body"])
        self.assertEqual(r["files"], [u"대학원장학금신청서류양식_2차_20262.zip"])
        self.assertIsNone(r["analysis"])
        self.assertIsNone(r["analyzed_ts"])
        self.assertTrue(r["available"])
        self.assertFalse(r["analyzing"])
        self.detail()
        self.assertEqual(self.fetched, [self.nid])          # 두 번째는 DB 에서

    def test_detail_survives_fetch_failure(self):
        def boom(conn, row, cfg, opener=None, boards=None):
            raise IOError("no network")
        notices.ensure_detail = boom
        r = self.detail()
        self.assertIsNone(r["body"])                        # 200 + body null
        self.assertEqual(r["files"], [])

    def test_detail_returns_saved_analysis(self):
        self.detail()
        notices.save_analysis(self.conn, self.nid, dict(NormalizeAnalysisTest.GOOD), now=555.0)
        r = self.detail()
        self.assertEqual(r["analysis"]["verdict"], u"가능")
        self.assertEqual(r["analyzed_ts"], 555.0)
        self.assertEqual(self.fetched, [self.nid])          # 다시 받지 않는다

    def test_detail_bad_id(self):
        self.assertRaises(server.ApiError, server.api_notices_detail, self.conn, {"id": ["x"]}, {})
        try:
            self.detail(99999)
        except server.ApiError as e:
            self.assertEqual(e.code, 404)
        else:
            self.fail(u"404 가 나야 한다")

    def test_analyze_starts_background_once(self):
        r = server.api_notices_analyze(self.conn, {}, {"id": self.nid})
        self.assertEqual(r, {"started": True, "available": True, "analyzing": True, "id": self.nid})
        self.assertEqual(len(self.analyze_calls), 1)
        self.assertTrue(self.detail()["analyzing"])
        again = server.api_notices_analyze(self.conn, {}, {"id": self.nid})
        self.assertFalse(again["started"])                  # 같은 글은 하나만
        self.assertTrue(again["analyzing"])
        self.assertEqual(len(self.analyze_calls), 1)

    def test_analyze_disabled_without_claude(self):
        notices.analysis_available = lambda: False
        r = server.api_notices_analyze(self.conn, {}, {"id": self.nid})
        self.assertEqual(r["started"], False)
        self.assertEqual(r["available"], False)
        self.assertEqual(self.analyze_calls, [])
        self.assertFalse(self.detail()["available"])

    def test_analyze_unknown_id(self):
        try:
            server.api_notices_analyze(self.conn, {}, {"id": 99999})
        except server.ApiError as e:
            self.assertEqual(e.code, 404)
        else:
            self.fail(u"404 가 나야 한다")

    def test_analyze_slots_are_bounded(self):
        self.assertEqual(server.NOTICE_ANALYZE_MAX, 2)

    def test_list_item_gets_has_analysis_and_verdict(self):
        it = self.get()["items"][0]
        self.assertFalse(it["has_analysis"])
        self.assertIsNone(it["verdict"])
        notices.save_analysis(self.conn, self.nid, dict(NormalizeAnalysisTest.GOOD))
        it = self.get()["items"][0]
        self.assertTrue(it["has_analysis"])
        self.assertEqual(it["verdict"], u"가능")

    def test_list_item_ignores_broken_analysis(self):
        notices.save_detail(self.conn, self.nid, u"b", [])
        self.conn.execute("UPDATE notice_detail SET analysis='not json' WHERE notice_id=?", (self.nid,))
        self.conn.commit()
        it = self.get()["items"][0]
        self.assertFalse(it["has_analysis"])
        self.assertIsNone(it["verdict"])

    def test_routes_registered(self):
        self.assertIn(("GET", "/api/notices/detail"), server.ROUTES)
        self.assertIn(("POST", "/api/notices/analyze"), server.ROUTES)


# ============================================================ §17 프로필 설정
class ProfileConfigTest(unittest.TestCase):
    def test_default_profile_shape(self):
        prof = default_cfg()["profile"]
        for k in (u"과정", u"학과", u"현재 장학", u"상태", u"관심"):
            self.assertIn(k, prof)
            self.assertTrue(prof[k])

    def test_normalize_drops_junk_and_caps_length(self):
        got = common.normalize_notices_config({"profile": {
            u"좋음": u"값  하나", u"": u"빈 키", u"빈 값": u"   ",
            u"목록": [u"a", u"b"], u"숫자": 3, u"중첩": {"x": 1}, u"긴 값": u"가" * 900}})["profile"]
        self.assertEqual(got[u"좋음"], u"값 하나")
        self.assertNotIn(u"", got)
        self.assertNotIn(u"빈 값", got)
        self.assertEqual(got[u"목록"], u"a, b")
        self.assertEqual(got[u"숫자"], u"3")
        self.assertNotIn(u"중첩", got)
        self.assertEqual(len(got[u"긴 값"]), common.NOTICE_PROFILE_MAX)

    def test_normalize_missing_profile(self):
        self.assertEqual(common.normalize_notices_config({})["profile"], {})
        self.assertEqual(common.normalize_notices_config({"profile": "nope"})["profile"], {})

    def test_normalize_caps_key_count(self):
        big = dict((u"k%d" % i, u"v") for i in range(common.NOTICE_PROFILE_KEYS_MAX + 10))
        got = common.normalize_notices_config({"profile": big})["profile"]
        self.assertEqual(len(got), common.NOTICE_PROFILE_KEYS_MAX)
