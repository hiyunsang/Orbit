# -*- coding: utf-8 -*-
"""tests/test_period.py — DESIGN §23.3 기간 판 API."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import server   # noqa: E402


class PeriodApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def get(self, start, end):
        return server.api_period_get(self.conn, {"start": [start], "end": [end]}, None)

    def test_empty_week(self):
        r = self.get("2026-09-21", "2026-09-27")
        self.assertEqual(len(r["days"]), 7)
        self.assertTrue(all(d["night"] is None and d["spend"] == 0 and d["todos_done"] == 0 for d in r["days"]))
        self.assertEqual(r["totals"], {"spend": 0, "nights": 0, "nights_done": 0, "workouts": 0, "todos_done": 0})
        self.assertEqual(r["budget"]["month"], "2026-09")

    def test_aggregates_per_day_and_totals(self):
        n1 = server.api_night_set(self.conn, {}, {"day": "2026-09-22", "kind": "workout"})
        server.api_night_confirm(self.conn, {}, {"id": n1["id"], "done": 1})
        server.api_night_set(self.conn, {}, {"day": "2026-09-23", "kind": "english"})
        server.api_night_set(self.conn, {}, {"day": "2026-09-24", "kind": "research"})
        server.api_spend_add(self.conn, {}, {"day": "2026-09-22", "amount": 9000})
        server.api_spend_add(self.conn, {}, {"day": "2026-09-22", "amount": 4500})
        t1 = server.api_todos_add(self.conn, {}, {"text": "a", "day": "2026-09-22"})["id"]
        server.api_todos_add(self.conn, {}, {"text": "b", "day": "2026-09-22"})
        server.api_todos_update(self.conn, {}, {"id": t1, "done": 1})
        r = self.get("2026-09-21", "2026-09-27")
        by = {d["date"]: d for d in r["days"]}
        d = by["2026-09-22"]
        self.assertEqual((d["night"]["kind"], d["night"]["done"], d["night"]["checked"]), ("workout", 1, True))
        self.assertEqual((d["spend"], d["todos_done"], d["todos_open"]), (13500, 1, 1))
        self.assertEqual(by["2026-09-23"]["night"]["checked"], False)
        self.assertEqual(by["2026-09-24"]["night"]["ask"], False)
        self.assertEqual(r["totals"], {"spend": 13500, "nights": 3, "nights_done": 1, "workouts": 1, "todos_done": 1})

    def test_outside_range_excluded(self):
        server.api_spend_add(self.conn, {}, {"day": "2026-09-20", "amount": 100})
        server.api_spend_add(self.conn, {}, {"day": "2026-09-28", "amount": 100})
        r = self.get("2026-09-21", "2026-09-27")
        self.assertEqual(r["totals"]["spend"], 0)

    def test_validation(self):
        with self.assertRaises(server.ApiError):
            self.get("2026-09-27", "2026-09-21")
        with self.assertRaises(server.ApiError):
            self.get("2026-01-01", "2026-12-31")
        with self.assertRaises(server.ApiError):
            self.get("2026-9-1", "2026-09-07")


if __name__ == "__main__":
    unittest.main()
