# -*- coding: utf-8 -*-
"""
tests/test_budget.py — DESIGN §21 예산: 배분·지출 핸들러와 금액 파서.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import server   # noqa: E402


class ParseAmountTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(common.parse_amount("9000 점심"), (9000, "점심"))

    def test_comma_and_won(self):
        self.assertEqual(common.parse_amount("12,000원 저녁 회식"), (12000, "저녁 회식"))

    def test_man_cheon(self):
        self.assertEqual(common.parse_amount("1.2만 책"), (12000, "책"))
        self.assertEqual(common.parse_amount("3천 커피"), (3000, "커피"))

    def test_note_first(self):
        self.assertEqual(common.parse_amount("택시 8500"), (8500, "택시"))

    def test_no_number(self):
        self.assertEqual(common.parse_amount("점심"), (None, "점심"))


class BudgetApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def plan(self, month, items):
        return server.api_budget_plan_set(self.conn, {}, {"month": month, "items": items})

    def spend(self, day, **kw):
        body = dict(day=day); body.update(kw)
        return server.api_spend_add(self.conn, {}, body)["id"]

    def get(self, date):
        return server.api_budget_get(self.conn, {"date": [date]}, None)

    def test_empty_month(self):
        r = self.get("2026-09-22")
        self.assertEqual(r["month"], "2026-09")
        self.assertEqual((r["plan"], r["day"], r["prev"]), ([], [], []))
        self.assertEqual(r["total"], {"amount": 0, "spent": 0, "left": 0})

    def test_plan_and_sums(self):
        self.plan("2026-09", [{"category": "식비", "amount": 300000}, {"category": "교통", "amount": 60000}])
        self.spend("2026-09-22", text="9000 점심", category="식비")
        self.spend("2026-09-03", amount=4500, category="식비", note="커피")
        self.spend("2026-09-10", amount=2000, category="교통")
        r = self.get("2026-09-22")
        by = {p["category"]: p for p in r["plan"]}
        self.assertEqual((by["식비"]["spent"], by["식비"]["left"]), (13500, 286500))
        self.assertEqual((by["교통"]["spent"], by["교통"]["left"]), (2000, 58000))
        self.assertEqual(r["total"], {"amount": 360000, "spent": 15500, "left": 344500})
        self.assertEqual([(d["amount"], d["note"]) for d in r["day"]], [(9000, "점심")],
                         "day 는 보고 있는 날짜의 지출만 (§21.4)")

    def test_unplanned_category_goes_to_extra_row(self):
        self.plan("2026-09", [{"category": "식비", "amount": 100000}])
        self.spend("2026-09-05", amount=15000, category="", note="택배")
        self.spend("2026-09-06", amount=5000, category="선물")
        r = self.get("2026-09-22")
        extra = [p for p in r["plan"] if p.get("extra")]
        self.assertEqual(len(extra), 1)
        self.assertEqual((extra[0]["category"], extra[0]["spent"], extra[0]["left"]), ("기타", 20000, -20000))
        self.assertEqual(r["total"]["spent"], 20000)

    def test_months_isolated(self):
        self.plan("2026-08", [{"category": "식비", "amount": 1}])
        self.plan("2026-09", [{"category": "식비", "amount": 2}])
        self.spend("2026-08-31", amount=100, category="식비")
        r = self.get("2026-09-01")
        self.assertEqual(r["plan"][0]["amount"], 2)
        self.assertEqual(r["total"]["spent"], 0, "8월 지출은 9월 합계에 들어가지 않는다 (1일 기준)")

    def test_plan_replaces_whole_month(self):
        self.plan("2026-09", [{"category": "식비", "amount": 1}, {"category": "교통", "amount": 2}])
        self.plan("2026-09", [{"category": "여가", "amount": 3}])
        self.assertEqual([p["category"] for p in self.get("2026-09-22")["plan"]], ["여가"])

    def test_prev_suggested_only_when_empty(self):
        self.plan("2026-07", [{"category": "식비", "amount": 5}])
        self.assertEqual(self.get("2026-09-22")["prev"], [{"category": "식비", "amount": 5}])
        self.plan("2026-09", [{"category": "식비", "amount": 6}])
        self.assertEqual(self.get("2026-09-22")["prev"], [])

    def test_plan_validation(self):
        for bad in ([{"category": "", "amount": 1}],
                    [{"category": "식비", "amount": -1}],
                    [{"category": "식비", "amount": 1}, {"category": "식비", "amount": 2}],
                    [{"category": "기타", "amount": 1}],
                    "no"):
            with self.assertRaises(server.ApiError):
                self.plan("2026-09", bad)
        with self.assertRaises(server.ApiError):
            self.plan("2026-13", [])

    def test_spend_validation(self):
        with self.assertRaises(server.ApiError):
            server.api_spend_add(self.conn, {}, {"day": "2026-09-22", "text": "점심"})
        with self.assertRaises(server.ApiError):
            server.api_spend_add(self.conn, {}, {"day": "2026-09-22", "amount": 0})
        with self.assertRaises(server.ApiError):
            server.api_spend_add(self.conn, {}, {"day": "2026-9-2", "amount": 100})

    def test_spend_update_and_delete(self):
        sid = self.spend("2026-09-22", text="9000 점심", category="식비")
        server.api_spend_update(self.conn, {}, {"id": sid, "amount": 9500, "note": "점심 김밥", "category": ""})
        d = self.get("2026-09-22")["day"][0]
        self.assertEqual((d["amount"], d["note"], d["category"]), (9500, "점심 김밥", ""))
        self.assertEqual(server.api_spend_delete(self.conn, {}, {"id": sid}), {"ok": True})
        self.assertEqual(server.api_spend_delete(self.conn, {}, {"id": sid}), {"ok": True})
        self.assertEqual(self.get("2026-09-22")["day"], [])
        with self.assertRaises(server.ApiError):
            server.api_spend_update(self.conn, {}, {"id": 999, "amount": 1})


if __name__ == "__main__":
    unittest.main()
