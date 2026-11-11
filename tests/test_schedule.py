# -*- coding: utf-8 -*-
"""tests/test_schedule.py — DESIGN §24 대학원 학사일정."""
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import schedule   # noqa: E402

RAW = [
    {"START": "2026-09-01", "END": "2026-09-07", "TITLE": "2026%ED%95%99%EB%85%84%EB%8F%84%202%ED%95%99%EA%B8%B0%20%EC%88%98%EA%B0%95%EC%8B%A0%EC%B2%AD%20%EC%A0%95%EC%A0%95", "GUBUN": "9", "HOLY": "0"},
    {"START": "2026-09-25", "END": "2026-09-25", "TITLE": "%EC%B6%94%EC%84%9D", "GUBUN": "9", "HOLY": "1"},
    {"START": "2026-10-12", "END": "2026-10-16", "TITLE": "%EC%8B%AC%EC%82%AC%20%EC%8B%A0%EC%B2%AD", "GUBUN": "2", "HOLY": "0"},
    {"START": "2026-09-30", "END": "2026-09-20", "TITLE": "%EC%97%AD%EC%88%9C", "GUBUN": "2", "HOLY": "0"},   # 종료<시작
    {"START": "bad", "END": "2026-09-20", "TITLE": "x", "GUBUN": "2"},                                       # 날짜 깨짐
    {"START": "2026-09-20", "END": "2026-09-20", "TITLE": "", "GUBUN": "2"},                                 # 빈 제목
    {"START": "2027-03-02", "END": "2027-03-02", "TITLE": "%EA%B0%9C%EA%B0%95", "GUBUN": "9", "HOLY": "0"},
]


class ParseTest(unittest.TestCase):
    def test_parse(self):
        items = schedule.parse_items(RAW)
        titles = [i["title"] for i in items]
        self.assertEqual(titles, ["2026학년도 2학기 수강신청 정정", "추석", "역순", "심사 신청", "개강"])
        self.assertEqual(items[2]["end"], "2026-09-30", "종료<시작이면 종료=시작")
        self.assertTrue(items[1]["holiday"])
        self.assertEqual(items[0]["gubun_name"], "공통")
        self.assertEqual(items[3]["gubun_name"], "대학원")


class UpcomingTest(unittest.TestCase):
    def setUp(self):
        self.items = schedule.parse_items(RAW)

    def test_ongoing_first_then_soonest(self):
        up = schedule.upcoming(self.items, "2026-09-03")
        self.assertEqual([i["title"] for i in up], ["2026학년도 2학기 수강신청 정정", "추석", "역순", "심사 신청"])
        self.assertTrue(up[0]["ongoing"])
        self.assertEqual((up[0]["d"], up[0]["d_end"]), (-2, 4))
        self.assertEqual(up[1]["d"], 22)

    def test_past_excluded_and_horizon(self):
        up = schedule.upcoming(self.items, "2026-09-26")
        self.assertNotIn("추석", [i["title"] for i in up])
        self.assertNotIn("개강", [i["title"] for i in up], "90일 밖은 제외")
        up2 = schedule.upcoming(self.items, "2026-12-15")
        self.assertIn("개강", [i["title"] for i in up2])

    def test_limit(self):
        self.assertEqual(len(schedule.upcoming(self.items, "2026-09-03", limit=2)), 2)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = {"data_dir": self.tmp}

    def test_save_and_load(self):
        items = schedule.parse_items(RAW)
        schedule.save_cache("2026", items, self.cfg)
        c = schedule.load_cache("2026", self.cfg)
        self.assertEqual(len(c["items"]), 5)
        self.assertTrue(os.path.isfile(schedule.cache_path("2026", self.cfg)))

    def test_items_for_uses_cache_when_fetch_fails(self):
        items = schedule.parse_items(RAW)
        schedule.save_cache("2026", items, self.cfg)
        orig = schedule.fetch_year
        schedule.fetch_year = lambda y, opener=None: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            got, fetched, err = schedule.items_for("2026", self.cfg, now=0)
            self.assertEqual(len(got), 5)
            self.assertIsNone(err)
        finally:
            schedule.fetch_year = orig

    def test_items_for_without_cache_reports_error(self):
        orig = schedule.fetch_year
        schedule.fetch_year = lambda y, opener=None: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            got, fetched, err = schedule.items_for("2031", self.cfg)
            self.assertEqual(got, [])
            self.assertIn("down", err)
        finally:
            schedule.fetch_year = orig


if __name__ == "__main__":
    unittest.main()
