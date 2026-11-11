# -*- coding: utf-8 -*-
"""tests/test_anki.py — DESIGN §31 Anki 모니터. 가짜 컬렉션으로 compute 를 검사한다."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import anki_stats   # noqa: E402

SCHEMA = """
CREATE TABLE col(id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
CREATE TABLE cards(id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
CREATE TABLE revlog(id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
CREATE TABLE decks(id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
"""


def day_start(now, rollover=4):
    lt = time.localtime(now)
    d0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, rollover, 0, 0, 0, 0, -1))
    return d0 - 86400 if now < d0 else d0


class ComputeTest(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.d0 = day_start(self.now)
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        crt = self.d0 - 100 * 86400
        self.crt = crt
        self.con.execute("INSERT INTO col VALUES(1,?,0,0,11,0,0,0,'{\"rollover\":4}','{}','{}','{}','{}')", (int(crt),))
        self.con.execute("INSERT INTO decks VALUES(1,'노랭이',0,0,NULL,NULL)")
        self.con.execute("INSERT INTO decks VALUES(2,'초록이::Day01',0,0,NULL,NULL)")
        today_idx = int((self.now - crt) // 86400)
        # 카드: 복습 큐 due 지남 2장, 오늘 1장, 내일 1장, 학습 중 1장(지금 due), 새 카드 3장
        rows = [(1, 1, 1, 2, today_idx - 3, 30), (2, 1, 1, 2, today_idx - 1, 25), (3, 1, 2, 2, today_idx, 5), (4, 1, 2, 2, today_idx + 1, 40)]
        for cid, nid, did, queue, due, ivl in rows:
            self.con.execute("INSERT INTO cards VALUES(?,?,?,0,0,0,2,?,?,?,2500,5,0,0,0,0,0,'')", (cid, nid, did, queue, due, ivl))
        self.con.execute("INSERT INTO cards VALUES(5,1,1,0,0,0,1,1,?,0,2500,1,0,1001,0,0,0,'')", (int(self.now) - 60,))
        for cid in (6, 7, 8):
            self.con.execute("INSERT INTO cards VALUES(?,1,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'')", (cid,))
        self.con.commit()

    def rev(self, when, cid, ease, typ=1, ms=6000):
        self.con.execute("INSERT INTO revlog VALUES(?,?,0,?,1,1,2500,?,?)", (int(when * 1000), cid, ease, ms, typ))

    def test_today_and_due(self):
        self.rev(self.d0 + 3600, 1, 3); self.rev(self.d0 + 3700, 2, 1); self.rev(self.d0 + 3800, 3, 4)   # 오늘 3장, 1장 틀림
        self.rev(self.d0 - 3600, 1, 3)                                                                   # 어제(밤 3시) 1장
        self.rev(self.d0 + 4000, 4, 4, typ=4)                                                            # 수동 변경은 세지 않는다
        self.con.commit()
        d = anki_stats.compute(self.con, self.now)
        self.assertEqual(d["today"], {"reviews": 3, "minutes": 0.3, "correct": 2, "accuracy": 67})
        self.assertEqual(d["due"], {"review": 3, "learn": 1, "new_waiting": 3})
        self.assertEqual(d["days"][-1]["n"], 3)
        self.assertEqual(d["days"][-2]["n"], 1)
        self.assertEqual(d["streak"], 2)
        self.assertEqual(d["decks"], [{"name": "노랭이", "n": 2}, {"name": "초록이", "n": 1}], "하위 덱은 최상위 이름으로 합친다")
        self.assertEqual(d["retention30"], 75)
        self.assertEqual(d["cards"], {"total": 8, "mature": 3})

    def test_streak_counts_from_yesterday_when_nothing_today(self):
        for i in (1, 2, 3):
            self.rev(self.d0 - i * 86400 + 3600, 1, 3)
        self.rev(self.d0 - 6 * 86400 + 3600, 1, 3)   # 끊긴 뒤 하나 더 — 안 센다
        self.con.commit()
        d = anki_stats.compute(self.con, self.now)
        self.assertEqual(d["today"]["reviews"], 0)
        self.assertEqual(d["streak"], 3)
        self.assertIsNone(d["today"]["accuracy"])

    def test_no_reviews(self):
        d = anki_stats.compute(self.con, self.now)
        self.assertEqual((d["today"]["reviews"], d["streak"], d["retention30"], d["last_review"]), (0, 0, None, None))

    def test_last_sync(self):
        self.assertIsNone(anki_stats.compute(self.con, self.now)["last_sync"], "ls=0 이면 None")
        self.con.execute("UPDATE col SET ls=?", (int((self.now - 3600) * 1000),))
        self.assertAlmostEqual(anki_stats.compute(self.con, self.now)["last_sync"], self.now - 3600, delta=1)


class SnapshotTest(unittest.TestCase):
    def test_no_collection(self):
        tmp = tempfile.mkdtemp()
        r = anki_stats.snapshot(appdata=tmp)
        self.assertFalse(r["available"])

    def test_reads_copy_of_file(self):
        tmp = tempfile.mkdtemp()
        prof = os.path.join(tmp, "Anki2", "User 1"); os.makedirs(prof)
        p = os.path.join(prof, "collection.anki2")
        con = sqlite3.connect(p); con.executescript(SCHEMA)
        now = time.time(); crt = day_start(now) - 10 * 86400
        con.execute("INSERT INTO col VALUES(1,?,0,0,11,0,0,0,'{}','{}','{}','{}','{}')", (int(crt),))
        con.execute("INSERT INTO decks VALUES(1,'예문',0,0,NULL,NULL)")
        con.execute("INSERT INTO cards VALUES(1,1,1,0,0,0,2,2,0,10,2500,1,0,0,0,0,0,'')")
        con.execute("INSERT INTO revlog VALUES(?,1,0,3,1,1,2500,5000,1)", (int((day_start(now) + 10) * 1000),))
        con.commit(); con.close()
        r = anki_stats.snapshot(now=now, appdata=tmp, force=True)
        self.assertTrue(r["available"])
        self.assertEqual(r["data"]["today"]["reviews"], 1)
        self.assertEqual(r["data"]["decks"], [{"name": "예문", "n": 1}])
        r2 = anki_stats.snapshot(now=now + 1, appdata=tmp)
        self.assertTrue(r2.get("cached"), "파일이 안 바뀌면 캐시")
        self.assertIn("connect", r2, "동기화 상태를 같이 싣는다")


class ConnectTest(unittest.TestCase):
    """§31.5 AnkiConnect 트리거 — 가짜 post 로. 시각은 실제 epoch 크기(T)로 — last_try 0 은 '아직 안 함' 이다."""
    T = 1700000000

    def setUp(self):
        anki_stats._connect.update({"present": None, "busy": False, "last_try": 0, "last_ok": 0, "error": None})
        self.calls = []

    def fake(self, ok=True, present=True):
        def post(url, payload, timeout):
            self.calls.append(payload["action"])
            if not present:
                raise OSError("connection refused")
            if payload["action"] == "version":
                return {"result": 6, "error": None}
            return {"result": None, "error": None if ok else "sync failed"}
        return post

    def test_absent(self):
        self.assertFalse(anki_stats.maybe_sync(now=self.T, post=self.fake(present=False), wait=True))
        st = anki_stats.connect_status()
        self.assertFalse(st["present"]); self.assertFalse(st["busy"]); self.assertIn("refused", st["error"])
        self.assertEqual(self.calls, ["version"], "없으면 sync 는 안 보낸다")

    def test_once_per_interval_and_force(self):
        T = self.T
        self.assertTrue(anki_stats.maybe_sync(now=T, post=self.fake(), wait=True))
        self.assertEqual(self.calls, ["version", "sync"])
        self.assertFalse(anki_stats.maybe_sync(now=T + 60, post=self.fake(), wait=True), "30분 안에는 다시 안 누름")
        self.assertTrue(anki_stats.maybe_sync(now=T + 60, post=self.fake(), wait=True, force=True), "새로고침은 바로")
        self.assertTrue(anki_stats.maybe_sync(now=T + 60 + anki_stats.SYNC_EVERY_SEC + 1, post=self.fake(), wait=True))
        st = anki_stats.connect_status()
        self.assertTrue(st["present"]); self.assertIsNone(st["error"]); self.assertFalse(st["busy"])
        self.assertEqual(st["last_ok"], T + 60 + anki_stats.SYNC_EVERY_SEC + 1)

    def test_error_recorded_and_retry_later(self):
        T = self.T
        self.assertFalse(anki_stats.maybe_sync(now=T, post=self.fake(ok=False), wait=True))
        st = anki_stats.connect_status()
        self.assertEqual(st["error"], "sync failed"); self.assertTrue(st["present"]); self.assertEqual(st["last_ok"], 0)
        self.assertFalse(anki_stats.maybe_sync(now=T + 60, post=self.fake(present=False), wait=True))
        self.assertEqual(self.calls, ["version", "sync"], "간격 안이면 묻지도 않는다")
        self.assertFalse(anki_stats.maybe_sync(now=T + anki_stats.SYNC_EVERY_SEC, post=self.fake(present=False), wait=True))
        self.assertFalse(anki_stats.connect_status()["present"], "없어졌으면 present=False 로 바뀐다")

    def test_sync_clears_file_cache(self):
        anki_stats._cache.update({"path": "x", "mtime": 5, "at": self.T, "data": {"a": 1}})
        anki_stats.maybe_sync(now=self.T, post=self.fake(), wait=True)
        self.assertIsNone(anki_stats._cache["mtime"], "끝나면 다음 요청이 파일을 다시 읽게")

    def test_snapshot_with_appdata_does_not_sync(self):
        anki_stats.snapshot(appdata=tempfile.mkdtemp())
        self.assertEqual(anki_stats.connect_status()["last_try"], 0)


if __name__ == "__main__":
    unittest.main()
