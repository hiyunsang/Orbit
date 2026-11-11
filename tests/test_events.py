# -*- coding: utf-8 -*-
"""tests/test_events.py — DESIGN §28 내 일정·마감. claude 호출은 가짜 함수로 바꾼다."""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import plans as events   # noqa: E402   # 표·모듈 이름은 plans (events 는 수집기 로그 표)
import server   # noqa: E402

TODAY = "2026-10-18"   # 일요일


class PureTest(unittest.TestCase):
    def test_next_occurrence(self):
        self.assertEqual(events.next_occurrence("2026-10-22", "", TODAY), "2026-10-22")
        self.assertEqual(events.next_occurrence("2026-10-01", "weekly", TODAY), "2026-10-22")   # 목요일 → 다음 목요일
        self.assertEqual(events.next_occurrence("2026-10-22", "weekly", TODAY), "2026-10-22")
        self.assertEqual(events.next_occurrence("2026-10-22", "weekly", TODAY, skip_until="2026-10-23"), "2026-10-29")
        self.assertIsNone(events.next_occurrence(None, "weekly", TODAY))

    def test_occurrences(self):
        self.assertEqual(events.occurrences("2026-10-01", "weekly", "2026-10-18", "2026-11-05"), ["2026-10-22", "2026-10-29", "2026-11-05"])
        self.assertEqual(events.occurrences("2026-10-24", "", "2026-10-18", "2026-10-31"), ["2026-10-24"])
        self.assertEqual(events.occurrences("2026-11-24", "", "2026-10-18", "2026-10-31"), [])


class EventsApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)
        self._async, self._parser, self._looker = events.ASYNC, events.parser, events.looker
        events.ASYNC = False
        self.parsed = {}
        self.looked = {}
        events.parser = lambda text, today: self.parsed.get(text, {})
        events.looker = lambda q, today: self.looked.get(q, {})
        self._today = server.common.today_str
        server.common.today_str = lambda cfg=None, now=None: TODAY

    def tearDown(self):
        events.ASYNC, events.parser, events.looker = self._async, self._parser, self._looker
        server.common.today_str = self._today
        self.conn.close()

    def add(self, text, day=None):
        return server.api_events_add(self.conn, {}, {"text": text, "day": day})["id"]

    def items(self, date=TODAY):
        return server.api_events_get(self.conn, {"date": [date]}, None)["items"]

    def test_explicit_day(self):
        self.add("열전달 숙제", "2026-10-24")
        it = self.items()[0]
        self.assertEqual((it["day"], it["status"], it["confidence"], it["d"]), ("2026-10-24", "ready", "confirmed", 6))

    def test_parsed_day_and_kind(self):
        self.parsed["24일 열전달 숙제"] = {"title": "열전달 숙제", "kind": "task", "day": "2026-10-24", "repeat": "none", "needs_lookup": False}
        self.add("24일 열전달 숙제")
        it = self.items()[0]
        self.assertEqual((it["title"], it["kind"], it["day"], it["status"]), ("열전달 숙제", "task", "2026-10-24", "ready"))

    def test_lookup_success_with_source(self):
        self.parsed["정밀공학회 초록 내야 해"] = {"title": "정밀공학회 초록 제출", "kind": "deadline", "day": None,
                                             "needs_lookup": True, "lookup_query": "2026 정밀공학회 초록 마감"}
        self.looked["2026 정밀공학회 초록 마감"] = {"day": "2026-10-30", "source_url": "https://www.kspe.or.kr/x", "confidence": "confirmed", "note": "접수 마감 10/30"}
        self.add("정밀공학회 초록 내야 해")
        it = self.items()[0]
        self.assertEqual((it["day"], it["confidence"], it["source_url"], it["d"]), ("2026-10-30", "confirmed", "https://www.kspe.or.kr/x", 12))

    def test_lookup_unknown_keeps_row_and_asks_for_date(self):
        self.parsed["기계학회 포스터"] = {"title": "기계학회 포스터", "kind": "deadline", "day": None, "needs_lookup": True, "lookup_query": "q"}
        self.looked["q"] = {"day": None, "confidence": "unknown", "source_url": "https://ksme.or.kr", "note": "못 찾음"}
        eid = self.add("기계학회 포스터")
        it = self.items()[0]
        self.assertEqual((it["status"], it["day"], it["confidence"]), ("ready", None, "unknown"))
        server.api_events_update(self.conn, {}, {"id": eid, "day": "2026-11-02"})
        it = self.items()[0]
        self.assertEqual((it["day"], it["confidence"], it["status"]), ("2026-11-02", "confirmed", "ready"), "사람이 적은 날짜가 최종 (§28.1)")

    def test_order_unknown_first_then_soonest_then_done(self):
        a = self.add("먼 것", "2026-12-01")
        b = self.add("가까운 것", "2026-10-20")
        self.parsed["모름"] = {"title": "모름", "kind": "task", "day": None, "needs_lookup": False}
        c = self.add("모름")
        d = self.add("끝낸 것", "2026-10-19")
        server.api_events_update(self.conn, {}, {"id": d, "done": 1})
        self.assertEqual([x["id"] for x in self.items()], [c, b, a, d])

    def test_weekly_next_and_skip(self):
        self.parsed["매주 목 수업 보조"] = {"title": "수업 보조", "kind": "event", "day": "2026-10-22", "repeat": "weekly", "needs_lookup": False}
        eid = self.add("매주 목 수업 보조")
        self.assertEqual(self.items()[0]["occ"], "2026-10-22")
        server.api_events_update(self.conn, {}, {"id": eid, "skip_until": "2026-10-23"})
        self.assertEqual(self.items()[0]["occ"], "2026-10-29")
        rng = events.in_range(self.conn, "2026-10-18", "2026-11-10", TODAY)
        self.assertEqual(sorted(k for k in rng), ["2026-10-29", "2026-11-05"])

    def test_done_and_delete(self):
        eid = self.add("x", "2026-10-20")
        server.api_events_update(self.conn, {}, {"id": eid, "done": 1})
        self.assertEqual(self.items()[0]["done"], 1)
        self.assertEqual(server.api_events_delete(self.conn, {}, {"id": eid}), {"ok": True})
        self.assertEqual(self.items(), [])

    def test_stale_parsing_shows_failed(self):
        events.ASYNC = True
        events.parser = None
        eid = self.add("아주 오래 걸리는 것")   # 스레드가 claude 를 찾으려 하지만 테스트에선 결과를 보지 않는다
        self.conn.execute("UPDATE plans SET status='parsing', updated_ts=? WHERE id=?", (time.time() - 3600, eid))
        self.conn.commit()
        self.assertEqual(self.items()[0]["status"], "failed")

    def test_validation(self):
        with self.assertRaises(server.ApiError):
            server.api_events_add(self.conn, {}, {"text": "  "})
        with self.assertRaises(server.ApiError):
            server.api_events_add(self.conn, {}, {"text": "x", "day": "2026-13-01"})
        eid = self.add("x", "2026-10-20")
        with self.assertRaises(server.ApiError):
            server.api_events_update(self.conn, {}, {"id": eid, "kind": "party"})
        with self.assertRaises(server.ApiError):
            server.api_events_update(self.conn, {}, {"id": 999, "done": 1})

    def test_period_carries_events(self):
        self.add("마감", "2026-10-20")
        r = server.api_period_get(self.conn, {"start": ["2026-10-19"], "end": ["2026-10-25"]}, None)
        by = {d["date"]: d for d in r["days"]}
        self.assertEqual(by["2026-10-20"]["events"][0]["title"], "마감")
        self.assertEqual(by["2026-10-21"]["events"], [])


if __name__ == "__main__":
    unittest.main()
