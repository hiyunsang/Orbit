# -*- coding: utf-8 -*-
"""tests/test_anki_mirror.py — DESIGN §59 Orbit 이 직접 AnkiWeb 에서 받아 오는 사본 + 덱 진행.
실제 AnkiWeb · PowerShell 은 부르지 않는다(가짜 runner). 사용자의 Anki 프로필도 읽지 않는다(가짜 prefs)."""
import io
import json
import os
import pickle
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import anki_mirror          # noqa: E402
import anki_mirror_worker   # noqa: E402
import anki_stats           # noqa: E402
import server               # noqa: E402
from tests.test_anki import SCHEMA, day_start   # noqa: E402


class WorkerTest(unittest.TestCase):
    def test_decide_never_uploads(self):
        d = anki_mirror_worker.decide
        self.assertEqual(d("NO_CHANGES"), "none")
        self.assertEqual(d("NORMAL_SYNC"), "normal")
        self.assertEqual(d("FULL_DOWNLOAD"), "download")
        self.assertEqual(d("FULL_SYNC"), "download", "사본에는 지킬 변경이 없다 — 통째로 맞출 땐 받기")
        self.assertEqual(d("FULL_UPLOAD"), "stop", "올리지 않는다")
        self.assertEqual(d("SOMETHING_NEW"), "stop")
        src = io.open(os.path.join(ROOT, "anki_mirror_worker.py"), encoding="utf-8").read()
        self.assertNotIn("upload=True", src, "올리기는 코드에 없다")
        self.assertEqual(src.count("upload=False"), 1)
        self.assertIn("col.sync_collection(auth_of(endpoint), False)", src, "미디어는 받지 않는다")
        self.assertIn('.replace(hkey, "***")', src, "오류 글에 열쇠가 섞이면 가린다")

    def test_read_auth_from_profile_readonly(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertEqual(anki_mirror_worker.read_auth(tmp), (None, None), "프로필이 없으면 없음")
            os.makedirs(os.path.join(tmp, "Anki2"))
            db = os.path.join(tmp, "Anki2", "prefs21.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE profiles(name TEXT PRIMARY KEY, data BLOB)")
            con.execute("INSERT INTO profiles VALUES('_global', ?)", (pickle.dumps({"ver": 1}),))
            con.execute("INSERT INTO profiles VALUES('logged-out', ?)", (pickle.dumps({"syncKey": None}),))
            con.execute("INSERT INTO profiles VALUES('me', ?)", (pickle.dumps({"syncKey": "KEY-FOR-TEST", "currentSyncUrl": "https://sync9.example/", "customSyncUrl": ""}),))
            con.commit(); con.close()
            before = os.path.getmtime(db)
            self.assertEqual(anki_mirror_worker.read_auth(tmp), ("KEY-FOR-TEST", "https://sync9.example/"))
            self.assertEqual(os.path.getmtime(db), before, "프로필은 읽기만")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = anki_mirror.MIRROR_DIR
        anki_mirror.MIRROR_DIR = os.path.join(self.tmp, "anki_mirror")
        anki_mirror._state.update({"busy": False, "last_try": 0, "last_ok": 0, "error": None, "required": None, "action": None})
        self.anki = os.path.join(self.tmp, "Anki")
        os.makedirs(os.path.join(self.anki, "app_packages", "anki"))
        for n in ("python3.dll", "python313.dll"):
            io.open(os.path.join(self.anki, n), "wb").close()
        self.cfg = {"anki_dir": self.anki}
        self.calls = []

    def tearDown(self):
        anki_mirror.MIRROR_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runner(self, result):
        def run(cmd, timeout):
            self.calls.append(cmd)
            if result is not None:
                with io.open(os.path.join(anki_mirror.mirror_dir(), "result.json"), "w", encoding="utf-8") as f:
                    f.write(json.dumps(result))
            return (0 if result and result.get("ok") else 1), ""
        return run

    def test_find_anki_picks_versioned_dll(self):
        d, dll = anki_mirror.find_anki(self.cfg)
        self.assertEqual(d, self.anki)
        self.assertTrue(dll.endswith("python313.dll"), "python3.dll(껍데기)이 아니라 판 번호가 붙은 dll")
        self.assertEqual(anki_mirror.find_anki({"anki_dir": os.path.join(self.tmp, "none")})[0] in (None, os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Anki")), True)

    def test_ps_command(self):
        cmd = anki_mirror.ps_command(r"C:\A\python313.dll", r"C:\O\anki_mirror_worker.py", [r"C:\O\data\anki_mirror", r"C:\Users\it's me\AppData\Roaming", r"C:\A"])
        self.assertIn('[DllImport(@"C:\\A\\python313.dll"', cmd)
        self.assertIn("Py_Main(5, @('python', 'C:\\O\\anki_mirror_worker.py'", cmd)
        self.assertIn("'C:\\Users\\it''s me\\AppData\\Roaming'", cmd, "작은따옴표는 겹쳐 적는다")
        self.assertTrue(cmd.startswith("$ErrorActionPreference = 'Stop'; Add-Type"))

    def test_off_until_enabled(self):
        self.assertFalse(anki_mirror.enabled())
        self.assertFalse(anki_mirror.maybe_sync(now=1000.0, cfg=self.cfg, wait=True, runner=self.runner({"ok": True})))
        self.assertEqual(self.calls, [], "켜지 않았으면 아무것도 부르지 않는다")
        self.assertIsNone(anki_stats.mirror_status())

    def test_sync_interval_force_and_error(self):
        anki_mirror.enable()
        self.assertTrue(anki_mirror.enabled())
        ok = {"ok": True, "required": "NO_CHANGES", "action": "none"}
        self.assertTrue(anki_mirror.maybe_sync(now=1000.0, cfg=self.cfg, wait=True, runner=self.runner(ok)))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("anki_mirror_worker.py", self.calls[0])
        st = anki_mirror.status()
        self.assertEqual((st["last_ok"], st["error"], st["required"], st["busy"]), (1000.0, None, "NO_CHANGES", False))
        self.assertFalse(anki_mirror.maybe_sync(now=1000.0 + 60, cfg=self.cfg, wait=True, runner=self.runner(ok)), "30분 안에는 다시 안 받는다")
        self.assertTrue(anki_mirror.maybe_sync(now=1000.0 + 60, cfg=self.cfg, wait=True, force=True, runner=self.runner(ok)), "새로고침은 바로")
        bad = {"ok": False, "error": "AnkiWeb 이 'FULL_UPLOAD' 를 요구합니다", "required": "FULL_UPLOAD", "action": "stop"}
        self.assertFalse(anki_mirror.maybe_sync(now=1000.0 + 3600, cfg=self.cfg, wait=True, runner=self.runner(bad)))
        st = anki_mirror.status()
        self.assertIn("FULL_UPLOAD", st["error"])
        self.assertEqual(st["last_ok"], 1060.0, "실패는 마지막 성공 때를 바꾸지 않는다")
        self.assertFalse(anki_mirror.maybe_sync(now=1000.0 + 7200, cfg=self.cfg, wait=True, runner=self.runner(None)), "결과 파일이 없으면 실패")
        self.assertIn("결과 없이", anki_mirror.status()["error"])
        self.assertEqual(anki_stats.mirror_status()["on"], True)

    def test_no_anki_installed(self):
        anki_mirror.enable()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = os.path.join(self.tmp, "nowhere")
        try:
            self.assertFalse(anki_mirror.maybe_sync(now=5.0, cfg={}, wait=True, runner=self.runner({"ok": True})))
            self.assertIn("Anki 가 없습니다", anki_mirror.status()["error"])
            self.assertEqual(self.calls, [])
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old

    def test_server_route_local_only_and_card_follows(self):
        self.assertIs(server.ROUTES[("POST", "/api/anki/mirror")], server.api_anki_mirror)
        self.assertIn("/api/anki/mirror", server._LOCAL_ONLY_POST, "이 PC 에서만 켜고 끈다")
        self.assertFalse(server._anki_mirror_on())
        anki_mirror.enable()
        self.assertTrue(server._anki_mirror_on())
        self.assertTrue(server._display_config()["anki_card"], "사본이 켜져 있으면 Anki 칸도 켜진다")
        r = server.api_anki_mirror(None, {}, {"on": False})
        self.assertEqual((r["ok"], r["on"]), (True, False))
        self.assertFalse(anki_mirror.enabled())


class ProgressTest(unittest.TestCase):
    """§59 덱 진행 — 맨 위 덱마다 본 것/전체 · 오늘 처음 본 것 · Day 태그."""

    def setUp(self):
        self.now = time.time()
        self.d0 = day_start(self.now)
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA + "CREATE TABLE notes(id INTEGER PRIMARY KEY, tags TEXT);")
        self.con.execute("INSERT INTO col VALUES(1,?,0,0,11,0,0,0,'{\"rollover\":4}','{}','{}','{}','{}')", (int(self.d0 - 50 * 86400),))
        self.con.execute("INSERT INTO decks VALUES(1,'초록이',0,0,NULL,NULL)")
        self.con.execute("INSERT INTO decks VALUES(2,'초록이\x1fDay 02',0,0,NULL,NULL)")
        self.con.execute("INSERT INTO decks VALUES(3,'토익',0,0,NULL,NULL)")
        for nid, tags in ((1, " Day01 별3 초록이 "), (2, " Day01 초록이 "), (3, " Day02 초록이 "), (4, " Day30 초록이 "), (5, " 토익 "), (6, " Holiday12 ")):
            self.con.execute("INSERT INTO notes VALUES(?,?)", (nid, tags))
        # 카드(id, nid, did, type, queue): 초록이 — Day01 둘은 봄, Day02 하나는 오늘 처음 봄, Day30 은 새 카드 / 토익 — 하나 봄 · 하나 새 카드
        for cid, nid, did, typ, queue in ((1, 1, 1, 2, 2), (2, 2, 1, 2, 2), (3, 3, 2, 1, 1), (4, 4, 1, 0, 0), (5, 5, 3, 2, 2), (6, 6, 3, 0, 0)):
            self.con.execute("INSERT INTO cards VALUES(?,?,?,0,0,0,?,?,0,1,2500,1,0,0,0,0,0,'')", (cid, nid, did, typ, queue))
        rv = lambda when, cid, typ: self.con.execute("INSERT INTO revlog VALUES(?,?,0,3,1,1,2500,5000,?)", (int(when * 1000), cid, typ))
        rv(self.d0 - 3 * 86400, 1, 0); rv(self.d0 - 2 * 86400, 2, 0); rv(self.d0 - 86400, 5, 0)      # 지난 날 처음 본 것
        rv(self.d0 + 600, 1, 1); rv(self.d0 + 700, 3, 0)                                            # 오늘: 복습 1 · 처음 1
        self.con.commit()

    def test_progress_lines(self):
        p = anki_stats.compute(self.con, self.now)["progress"]
        self.assertEqual([x["name"] for x in p], ["초록이", "토익"], "오늘 한 덱이 먼저 — 하위 덱은 맨 위 덱으로 합친다")
        c = p[0]
        self.assertEqual((c["total"], c["learned"], c["new_today"], c["today"]), (4, 3, 1, 2))
        self.assertEqual((c["day"], c["days"]), (2, 30), "본 카드 가운데 가장 큰 Day / 마지막 Day")
        t = p[1]
        self.assertEqual((t["total"], t["learned"], t["new_today"], t["day"], t["days"]), (2, 1, 0, None, None), "Day 태그가 없으면 뺀다(Holiday12 는 Day 가 아니다)")

    def test_without_notes_table_still_counts(self):
        self.con.execute("DROP TABLE notes")
        p = anki_stats.compute(self.con, self.now)["progress"]
        self.assertEqual((p[0]["total"], p[0]["learned"], p[0]["day"]), (4, 3, None))


if __name__ == "__main__":
    unittest.main()
