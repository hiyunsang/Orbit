# -*- coding: utf-8 -*-
"""
tests/test_night.py — DESIGN §19 밤 일정.
common.night_window / engine.night_research_sec 순수 함수 + server 의 밤 일정 핸들러 직접 호출.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import os
import sys
import datetime
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import engine   # noqa: E402
import server   # noqa: E402

CFG = dict(common.DEFAULT_CONFIG)


def ts(date_str, hh, mm=0):
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(hour=0, minute=0).timestamp() + hh * 3600 + mm * 60


def span(cat, a, b):
    return {"category": cat, "start": a, "end": b}


# ------------------------------------------------------------------ 순수 함수
class NightWindowTest(unittest.TestCase):
    def test_default_is_20_to_next_4(self):
        st, en = common.night_window("2026-09-12", CFG)
        self.assertEqual(st, ts("2026-09-12", 20))
        self.assertEqual(en, ts("2026-09-13", 4))

    def test_follows_config(self):
        cfg = dict(CFG, night_start_hour=19)
        st, _ = common.night_window("2026-09-12", cfg)
        self.assertEqual(st, ts("2026-09-12", 19))

    def test_bad_config_falls_back_to_default(self):
        for bad in (0, 4, 3, 30, -1):
            cfg = dict(CFG, night_start_hour=bad)
            st, en = common.night_window("2026-09-12", cfg)
            self.assertEqual(st, ts("2026-09-12", 20),
                             "night_start_hour=%r 은 기본값 20 으로 떨어져야 한다 (§19.6)" % bad)
            self.assertLess(st, en)

    def test_never_exceeds_day_end(self):
        st, en = common.night_window("2026-09-12", CFG)
        _, dend = common.day_window("2026-09-12", CFG)
        self.assertEqual(en, dend, "밤의 끝은 하루의 끝과 같아야 한다 (§19.2)")
        self.assertLessEqual(st, en)


class NightResearchSecTest(unittest.TestCase):
    def setUp(self):
        self.a, self.b = common.night_window("2026-09-12", CFG)

    def test_empty(self):
        self.assertEqual(engine.night_research_sec(None, self.a, self.b), 0.0)
        self.assertEqual(engine.night_research_sec([], self.a, self.b), 0.0)

    def test_counts_only_work_categories(self):
        sp = [
            span("focus", ts("2026-09-12", 21), ts("2026-09-12", 22)),        # 3600 ○
            span("experiment", ts("2026-09-12", 22), ts("2026-09-12", 22, 30)),  # 1800 ○
            span("meeting", ts("2026-09-12", 23), ts("2026-09-12", 23, 10)),  # 600 ○
            span("distraction", ts("2026-09-12", 20), ts("2026-09-12", 21)),  # ×
            span("break", ts("2026-09-12", 23, 30), ts("2026-09-13", 0)),     # ×
            span("neutral", ts("2026-09-13", 0), ts("2026-09-13", 1)),        # ×
            span("unknown", ts("2026-09-13", 1), ts("2026-09-13", 2)),        # ×
            span("remote", ts("2026-09-13", 2), ts("2026-09-13", 3)),         # ×
            span("off", ts("2026-09-13", 3), ts("2026-09-13", 4)),            # ×
        ]
        self.assertAlmostEqual(engine.night_research_sec(sp, self.a, self.b), 6000.0)

    def test_clips_to_window(self):
        # 19:30~20:30 → 밤 구간과 겹치는 절반만
        sp = [span("focus", ts("2026-09-12", 19, 30), ts("2026-09-12", 20, 30))]
        self.assertAlmostEqual(engine.night_research_sec(sp, self.a, self.b), 1800.0)

    def test_no_overlap_is_zero(self):
        sp = [span("focus", ts("2026-09-12", 13), ts("2026-09-12", 18))]
        self.assertEqual(engine.night_research_sec(sp, self.a, self.b), 0.0)

    def test_is_pure(self):
        """I/O 도 win32 도 없이 dict 목록만 받는다 (§7)."""
        sp = [span("focus", self.a, self.a + 60)]
        before = [dict(x) for x in sp]
        engine.night_research_sec(sp, self.a, self.b)
        self.assertEqual(sp, before, "입력을 변형하면 안 된다")


# ------------------------------------------------------------------ 서버 핸들러
class NightApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)
        self._cfg = server.CFG
        server.CFG = dict(CFG)

    def tearDown(self):
        server.CFG = self._cfg
        self.conn.close()

    def get(self, date):
        return server.api_night_get(self.conn, {"date": [date]}, None)

    def test_empty_day(self):
        r = self.get("2026-09-12")
        self.assertIsNone(r["today"])
        self.assertEqual(r["pending"], [])
        self.assertEqual([k["id"] for k in r["kinds"]],
                         ["appointment", "english", "workout", "study", "research", "rest"])   # §30 '공부' 추가
        self.assertTrue(r["workout"]["due"], "운동 기록이 없으면 오늘이 차례다")
        self.assertIsNone(r["workout"]["last"])

    def test_set_is_one_row_per_day(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "english"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "workout"})
        rows = self.conn.execute("SELECT * FROM night WHERE day='2026-09-12'").fetchall()
        self.assertEqual(len(rows), 1, "하루 한 행이어야 한다 (§19.3)")
        self.assertEqual(self.get("2026-09-12")["today"]["kind"], "workout")

    def test_changing_kind_resets_confirmation(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "english"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 1})
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "workout"})
        t = self.get("2026-09-12")["today"]
        self.assertEqual(t["done"], 0)
        self.assertIsNone(t["checked_ts"], "종류를 바꾸면 이전 확인은 무효다 (§19.7)")

    # §23.1 '다른 걸 했음' — 종류와 이행을 한 요청으로
    def test_set_with_done_marks_checked(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "english"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "workout", "done": 1})
        row = self.conn.execute("SELECT * FROM night WHERE day='2026-09-11'").fetchone()
        self.assertEqual((row["kind"], row["done"]), ("workout", 1))
        self.assertIsNotNone(row["checked_ts"])

    def test_set_with_done_ignored_for_non_ask_kind(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "research", "done": 1})
        row = self.conn.execute("SELECT * FROM night WHERE day='2026-09-11'").fetchone()
        self.assertEqual(row["done"], 0)
        self.assertIsNone(row["checked_ts"], "연구·쉼은 확인하지 않는 종류라 done 을 무시한다 (§23.1)")

    def test_set_without_done_unchanged(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "english"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 1})
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "english"})
        row = self.conn.execute("SELECT * FROM night WHERE day='2026-09-11'").fetchone()
        self.assertEqual(row["done"], 1, "done 을 안 보내면 같은 종류 재등록은 이전 확인을 지우지 않는다 (§19)")

    def test_bad_kind_rejected(self):
        with self.assertRaises(server.ApiError) as cm:
            server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "gym"})
        self.assertEqual(cm.exception.code, 400)

    def test_pending_only_ask_kinds(self):
        for kind in ("english", "research", "rest"):
            server.api_night_set(self.conn, {}, {"day": "2026-09-09", "kind": kind})
            self.conn.execute("UPDATE night SET day=? WHERE day='2026-09-09'",
                              ("2026-09-1%d" % {"english": 1, "research": 0, "rest": 0}[kind],))
        # 깔끔하게 다시: 각 종류를 다른 날짜에 하나씩
        self.conn.execute("DELETE FROM night")
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "english"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-10", "kind": "research"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-09", "kind": "rest"})
        ids = [p["kind"] for p in self.get("2026-09-12")["pending"]]
        self.assertEqual(ids, ["english"],
                         "연구·쉼은 묻지 않는다 — 연구는 측정하고 쉼은 확인할 것이 없다 (§19.8)")

    def test_pending_window_is_three_days(self):
        for d in ("2026-09-11", "2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07"):
            server.api_night_set(self.conn, {}, {"day": d, "kind": "workout"})
        days = [p["day"] for p in self.get("2026-09-12")["pending"]]
        self.assertEqual(days, ["2026-09-11", "2026-09-10", "2026-09-09"],
                         "최근 %d일만 묻고 최신이 위다 (§19.8). 투두 carried 와 달리 기간을 자른다 — "
                         "할 일은 아직 할 수 있지만 지난 밤의 이행 기록은 기억이 부정확하다."
                         % server.NIGHT_ASK_DAYS)

    def test_pending_excludes_checked_and_today(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "english"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "workout"})
        self.assertEqual(len(self.get("2026-09-12")["pending"]), 1)
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 0})
        self.assertEqual(self.get("2026-09-12")["pending"], [],
                         "확인한 행과 오늘 행은 묻지 않는다")

    def test_confirm_rejects_non_ask_kinds(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "research"})
        with self.assertRaises(server.ApiError) as cm:
            server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 1})
        self.assertEqual(cm.exception.code, 400)

    def test_confirm_missing_id(self):
        with self.assertRaises(server.ApiError) as cm:
            server.api_night_confirm(self.conn, {}, {"id": 999, "done": 1})
        self.assertEqual(cm.exception.code, 404)

    def test_confirm_records_time_even_when_not_done(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "workout"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 0})
        t = self.get("2026-09-12")["today"]
        self.assertEqual(t["done"], 0)
        self.assertIsNotNone(t["checked_ts"], "'안 했음' 도 확인한 사실은 남긴다")

    def test_delete_is_idempotent(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "rest"})
        self.assertEqual(server.api_night_delete(self.conn, {}, {"id": r["id"]}), {"ok": True})
        self.assertEqual(server.api_night_delete(self.conn, {}, {"id": r["id"]}), {"ok": True})
        self.assertIsNone(self.get("2026-09-12")["today"])

    # ---- 격일 운동 (§19.6) ----
    def _did_workout(self, day):
        r = server.api_night_set(self.conn, {}, {"day": day, "kind": "workout"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 1})

    def test_workout_due_table(self):
        cases = [
            ("2026-09-12", 0, False),   # 오늘 이미 함
            ("2026-09-11", 1, False),   # 어제 함 — 격일이니 오늘은 쉬는 날
            ("2026-09-10", 2, True),    # 그제 함 — 차례
            ("2026-09-09", 3, True),    # 하루 밀려도 계속 따라온다
        ]
        for last, gap, due in cases:
            self.conn.execute("DELETE FROM night")
            self._did_workout(last)
            w = self.get("2026-09-12")["workout"]
            self.assertEqual((w["last"], w["gap"], w["due"]), (last, gap, due),
                             "마지막 운동 %s 이면 gap=%d due=%s 여야 한다 (§19.6)" % (last, gap, due))

    def test_unconfirmed_plan_does_not_count_as_workout(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "workout"})
        w = self.get("2026-09-12")["workout"]
        self.assertIsNone(w["last"], "확인되지 않은 계획을 운동한 것으로 세면 안 된다 (§19.6)")
        self.assertTrue(w["due"])

    def test_not_done_does_not_count(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-11", "kind": "workout"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 0})
        self.assertIsNone(self.get("2026-09-12")["workout"]["last"])

    # ---- /api/day 에 실리는 값 (§19.7) ----
    def test_day_payload_research_is_measured(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "research"})
        a, b = common.night_window("2026-09-12", CFG)
        sp = [span("focus", a, a + 3600)]           # 3600 >= 1800 → 이행
        n = server._night_for_day(self.conn, "2026-09-12", sp)
        self.assertEqual(n["research_sec"], 3600.0)
        self.assertEqual(n["done"], 1)
        self.assertIsNone(n["checked_ts"], "측정값이라 사람이 확인한 시각이 없다")
        self.assertEqual(n["start"], a)
        self.assertEqual(n["end"], b)

    def test_day_payload_research_below_threshold(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "research"})
        a, _ = common.night_window("2026-09-12", CFG)
        n = server._night_for_day(self.conn, "2026-09-12", [span("focus", a, a + 600)])
        self.assertEqual(n["done"], 0, "임계값 미만이면 이행이 아니다")

    def test_day_payload_read_path_does_not_write(self):
        server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "research"})
        a, _ = common.night_window("2026-09-12", CFG)
        server._night_for_day(self.conn, "2026-09-12", [span("focus", a, a + 7200)])
        row = self.conn.execute("SELECT done FROM night WHERE day='2026-09-12'").fetchone()
        self.assertEqual(row["done"], 0,
                         "읽기 경로가 DB 의 done 을 덮으면 안 된다 (§19.7 notices GET 태도와 같다)")

    def test_day_payload_none_when_no_plan(self):
        self.assertIsNone(server._night_for_day(self.conn, "2026-09-12", []))

    def test_day_payload_manual_kind_keeps_user_answer(self):
        r = server.api_night_set(self.conn, {}, {"day": "2026-09-12", "kind": "english"})
        server.api_night_confirm(self.conn, {}, {"id": r["id"], "done": 1})
        n = server._night_for_day(self.conn, "2026-09-12", [])
        self.assertEqual(n["done"], 1)
        self.assertIsNotNone(n["checked_ts"])
        self.assertNotIn("research_sec", n)


class NightRoutingTest(unittest.TestCase):
    def test_routes_registered(self):
        for m, p in (("GET", "/api/night"), ("POST", "/api/night"),
                     ("POST", "/api/night/confirm"), ("POST", "/api/night/delete")):
            self.assertIn((m, p), server.ROUTES, "%s %s 라우트가 없다 (§19.7)" % (m, p))

    def test_config_exposes_night_start_hour(self):
        self.assertIn("night_start_hour", server._display_config(),
                      "프론트가 밤 밴드를 그리려면 이 값이 필요하다 (§19.7)")


if __name__ == "__main__":
    unittest.main()
