# -*- coding: utf-8 -*-
"""
tests/test_server_day.py — server.api_day 가 window.start 시점 상태를 복원하는지 (자정 04:00 경계)
+ 입력 검증(비정상 날짜·Infinity) 을 임시 DB 로 확인한다. HTTP 없이 핸들러 함수를 직접 호출.
"""
import os
import sys
import copy
import json
import datetime
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402
import server  # noqa: E402

BASE = datetime.datetime(2026, 9, 2)


def T(h, m=0, s=0):
    return (BASE + datetime.timedelta(hours=h, minutes=m, seconds=s)).timestamp()


CODE = "C:\\x\\Code.exe"


class ServerDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_srv_")
        self.db = os.path.join(self.tmp, "t.db")
        self._old_env = os.environ.get("MAENG_WORKLOG_DB")
        os.environ["MAENG_WORKLOG_DB"] = self.db
        self._old_cfg = server.CFG
        server.CFG = copy.deepcopy(common.DEFAULT_CONFIG)
        self.conn = common.connect(server.CFG, self.db)
        self._old_now = common.now_ts
        common.now_ts = lambda: T(30)  # 다음날 10:00 (오늘 아님)

    def tearDown(self):
        common.now_ts = self._old_now
        server.CFG = self._old_cfg
        self.conn.close()
        if self._old_env is None:
            os.environ.pop("MAENG_WORKLOG_DB", None)
        else:
            os.environ["MAENG_WORKLOG_DB"] = self._old_env
        for n in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, n))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def ev(self, kind, ts, exe="", title=""):
        self.conn.execute("INSERT INTO events(ts, kind, exe, title, pid, extra) VALUES(?,?,?,?,?,?)",
                          (ts, kind, exe, title, None, None))
        self.conn.commit()

    def sess(self, start, end):
        self.conn.execute("INSERT INTO sessions(start_ts, last_seen, end_ts, end_reason) VALUES(?,?,?,?)",
                          (start, end, end, "stop"))
        self.conn.commit()

    def brief(self, day):
        return [(s["category"], s["start"], s["end"]) for s in day["spans"] if s["category"] != "off"]

    def test_overnight_lock_restored_at_0400(self):
        # 전날 09:00 시작, 19:00 잠금, 22:00~04:00 이벤트 없음, 09:00 해제
        self.ev("start", T(-19)); self.ev("app", T(-19), CODE, "a.py")
        self.ev("lock", T(-9))
        self.ev("unlock", T(9)); self.ev("app", T(9), CODE, "a.py")
        self.ev("stop", T(12))
        self.sess(T(-19), T(12))
        day = server.api_day(self.conn, {"date": ["2026-09-02"]}, {})
        cats = [c for c, _, _ in self.brief(day)]
        # 04:00 시점 잠금 상태가 복원되어 away 계열이어야 한다(unknown 아님).
        # §11.1 이후 출근 전 away 는 absent 로 바뀐다.
        self.assertEqual(cats[0], "absent", day["spans"])
        self.assertEqual(self.brief(day)[0][1], T(4))
        self.assertEqual(day["summary"]["first_active"], T(9))
        self.assertAlmostEqual(day["summary"]["active_sec"], 3 * 3600, delta=1)

    def test_overnight_idle_and_app_restored(self):
        # 잠금 없이 전날 18:00 idle_on(소급) 후 방치, 전경창은 21:00 이후 그대로
        self.ev("start", T(-19)); self.ev("app", T(-19), CODE, "a.py")
        self.ev("idle_on", T(-10))
        self.ev("idle_off", T(9)); self.ev("app", T(9), CODE, "b.py")
        self.ev("stop", T(12))
        self.sess(T(-19), T(12))
        rules = [{"target": "exe", "pattern": "Code.exe", "category": "focus", "priority": 100,
                  "is_regex": False, "idle_limit_sec": None, "enabled": True}]
        old = common.load_rules
        common.load_rules = lambda: rules
        try:
            day = server.api_day(self.conn, {"date": ["2026-09-02"]}, {})
        finally:
            common.load_rules = old
        # idle 상태 복원 → 04:00~09:00 은 away 계열(§11.1: 출근 전이므로 absent)
        self.assertEqual(self.brief(day)[0], ("absent", T(4), T(9)))
        self.assertEqual(day["summary"]["first_active"], T(9))
        self.assertIn("absent_sec", day["summary"])
        self.assertAlmostEqual(day["summary"]["absent_sec"], 5 * 3600, delta=1)

    def test_prefix_picks_latest_in_each_group(self):
        # lo(= 04:00-6h = 전날 22:00) 이전: lock(19:00) 뒤 unlock(20:00), idle_on(20:30) 뒤 idle_off(21:00)
        # → 그룹별로 (ts,id) 가 가장 늦은 1건(unlock, idle_off)만 앞에 붙어야 한다(kind 별 조회로 바꾼 뒤에도 동일)
        self.ev("start", T(-19)); self.ev("app", T(-19), CODE, "a.py")
        self.ev("lock", T(-9)); self.ev("unlock", T(-8))
        self.ev("idle_on", T(-8, 30)); self.ev("idle_off", T(-7))
        self.ev("app", T(-7), CODE, "b.py")
        self.sess(T(-19), T(12))
        sessions = server._rows(self.conn, "SELECT * FROM sessions")
        lo = common.day_window("2026-09-02", server.CFG)[0] - 6 * 3600
        pre = server._state_prefix_events(self.conn, sessions, lo)
        kinds = [e["kind"] for e in pre]
        self.assertEqual(kinds, ["start", "unlock", "idle_off", "app"])
        self.assertEqual([e["title"] for e in pre if e["kind"] == "app"], ["b.py"])
        self.assertEqual([float(e["ts"]) for e in pre], sorted(float(e["ts"]) for e in pre))
        # 같은 ts 에 두 kind 가 있으면 id 가 큰 쪽(나중에 기록된 것)
        self.ev("lock", T(-5)); self.ev("unlock", T(-5))
        pre = server._state_prefix_events(self.conn, sessions, lo)
        self.assertIn("unlock", [e["kind"] for e in pre])
        self.assertNotIn("lock", [e["kind"] for e in pre])

    def test_ensure_indexes_idempotent(self):
        server.ensure_indexes(self.conn)
        server.ensure_indexes(self.conn)
        names = [r[1] for r in self.conn.execute("PRAGMA index_list(events)")]
        self.assertIn("idx_events_kind_ts", names)
        self.assertIn("idx_events_ts", names)
        plan = " ".join(str(tuple(r)) for r in self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM events WHERE kind=? AND ts>=? AND ts<? "
            "ORDER BY ts DESC, id DESC LIMIT 1", ("lock", 0, 1)))
        self.assertIn("idx_events_kind_ts", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_stopped_previous_day_stays_off(self):
        self.ev("start", T(-19)); self.ev("app", T(-19), CODE, "a.py"); self.ev("stop", T(-6))
        self.sess(T(-19), T(-6))
        day = server.api_day(self.conn, {"date": ["2026-09-02"]}, {})
        self.assertEqual(self.brief(day), [])

    def test_bad_year_is_400(self):
        with self.assertRaises(server.ApiError) as cm:
            server.api_day(self.conn, {"date": ["1899-01-01"]}, {})
        self.assertEqual(cm.exception.code, 400)

    # ---------------------------------------------------------------- §11.2 /api/range
    def _seed_two_days(self):
        # 09-01: 09:00~18:00, 09-02: 09:00~12:00 (T 는 09-02 00:00 기준)
        self.ev("start", T(-15)); self.ev("app", T(-15), CODE, "a.py")
        self.ev("stop", T(-6))
        self.sess(T(-15), T(-6))
        self.ev("start", T(9)); self.ev("app", T(9), CODE, "b.py")
        self.ev("stop", T(12))
        self.sess(T(9), T(12))

    def test_range_three_days(self):
        self._seed_two_days()
        q = {"start": ["2026-09-01"], "end": ["2026-09-03"]}
        res = server.api_range(self.conn, q, {})
        self.assertEqual(res["start"], "2026-09-01")
        self.assertEqual(res["end"], "2026-09-03")
        self.assertEqual([d["date"] for d in res["days"]], ["2026-09-01", "2026-09-02", "2026-09-03"])
        # 최상위 공통 필드는 한 번만, 각 원소에는 date/window/summary 만(spans=0)
        for k in ("categories", "config", "status"):
            self.assertIn(k, res)
        for d in res["days"]:
            self.assertEqual(set(d.keys()), {"date", "window", "summary"})
            self.assertNotIn("categories", d)
        s1, s2, s3 = [d["summary"] for d in res["days"]]
        self.assertAlmostEqual(s1["active_sec"], 9 * 3600, delta=1)
        self.assertAlmostEqual(s2["active_sec"], 3 * 3600, delta=1)
        self.assertEqual(s3["active_sec"], 0)
        self.assertEqual(res["days"][1]["window"]["start"], T(4))
        # spans=1 이면 spans 포함, /api/day 와 동일
        q["spans"] = ["1"]
        res2 = server.api_range(self.conn, q, {})
        day = server.api_day(self.conn, {"date": ["2026-09-02"]}, {})
        self.assertEqual(res2["days"][1]["spans"], day["spans"])
        self.assertEqual(res2["days"][1]["summary"], day["summary"])
        self.assertIn("spans", res2["days"][0])

    def test_range_62_day_limit(self):
        # 62일(포함)은 허용, 63일은 400
        ok = server.api_range(self.conn, {"start": ["2026-01-01"], "end": ["2026-03-03"]}, {})
        self.assertEqual(len(ok["days"]), 62)
        self.assertEqual(ok["days"][0]["date"], "2026-01-01")
        self.assertEqual(ok["days"][-1]["date"], "2026-03-03")
        with self.assertRaises(server.ApiError) as cm:
            server.api_range(self.conn, {"start": ["2026-01-01"], "end": ["2026-03-04"]}, {})
        self.assertEqual(cm.exception.code, 400)

    def test_range_bad_params_400(self):
        for q in ({"start": ["2026-09-02"]},
                  {"start": ["2026-09-02"], "end": ["2026/09/03"]},
                  {"start": ["2026-09-05"], "end": ["2026-09-02"]}):
            with self.assertRaises(server.ApiError) as cm:
                server.api_range(self.conn, q, {})
            self.assertEqual(cm.exception.code, 400, q)

    def test_range_future_days_empty(self):
        # now = 2026-09-03 10:00 → 09-04, 09-05 는 미래: 이벤트가 있어도 조회하지 않고 빈 결과
        self.ev("start", T(48 + 9)); self.ev("app", T(48 + 9), CODE, "z.py")
        self.sess(T(48 + 9), T(48 + 12))
        res = server.api_range(self.conn, {"start": ["2026-09-03"], "end": ["2026-09-05"], "spans": ["1"]}, {})
        self.assertEqual([d["date"] for d in res["days"]], ["2026-09-03", "2026-09-04", "2026-09-05"])
        for d in res["days"][1:]:
            self.assertEqual(d["spans"], [])
            self.assertIsNone(d["summary"]["first_active"])
            self.assertEqual(d["summary"]["active_sec"], 0)
            self.assertFalse(d["summary"]["in_progress"])
            self.assertEqual(d["window"]["start"], common.day_window(d["date"], server.CFG)[0])

    def test_restart_route_registered(self):
        self.assertIs(server.ROUTES[("POST", "/api/restart")], server.api_restart)
        self.assertIs(server.ROUTES[("GET", "/api/range")], server.api_range)
        self.assertTrue(callable(getattr(server.api_restart, "after_send", None)))

    def _run_restart_worker(self, popen):
        """_restart_worker 를 Popen·os._exit·_SERVER 를 바꿔치기해 실행. (exited, closed) 를 돌려준다."""
        import subprocess
        calls = {"exit": False, "closed": False}

        class FakeSrv(object):
            def server_close(self):
                calls["closed"] = True

        class Exit(BaseException):
            pass

        def fake_exit(code):
            calls["exit"] = True
            raise Exit()

        old = (subprocess.Popen, os._exit, server._SERVER, server.RESTART_DELAY_SEC, server.RESTART_CHILD_WATCH_SEC)
        subprocess.Popen, os._exit, server._SERVER = popen, fake_exit, FakeSrv()
        server.RESTART_DELAY_SEC, server.RESTART_CHILD_WATCH_SEC = 0, 0.25
        try:
            try:
                server._restart_worker()
            except Exit:
                pass
        finally:
            subprocess.Popen, os._exit, server._SERVER, server.RESTART_DELAY_SEC, server.RESTART_CHILD_WATCH_SEC = old
        return calls["exit"], calls["closed"]

    def test_restart_keeps_server_when_popen_fails(self):
        def popen(*a, **k):
            raise OSError("pythonw 없음")
        self.assertEqual(self._run_restart_worker(popen), (False, False))

    def test_restart_keeps_server_when_child_dies_immediately(self):
        class Dead(object):
            pid = 1

            def poll(self):
                return 1
        self.assertEqual(self._run_restart_worker(lambda *a, **k: Dead()), (False, False))

    def test_restart_closes_and_exits_when_child_alive(self):
        class Alive(object):
            pid = 1

            def poll(self):
                return None
        self.assertEqual(self._run_restart_worker(lambda *a, **k: Alive()), (True, True))

    def test_override_rejects_infinity(self):
        body = json.loads('{"start": 1, "end": Infinity, "label": "focus"}')
        with self.assertRaises(server.ApiError) as cm:
            server.api_override(self.conn, {}, body)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM overrides").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
