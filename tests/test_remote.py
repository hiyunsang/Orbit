# -*- coding: utf-8 -*-
"""
tests/test_remote.py — Jump Desktop 원격 감지 (DESIGN §13.3)

- remote_jump.parse_jump_event: 4종 문자열, UTC→로컬 변환, 기록번호
- remote_jump.build_intervals: 정상 쌍 / closed 누락+같은 peer 다음 established / max_session_hours 캡 /
  진행 중 None / 중복 established
- tracker.backfill_jump: 같은 conn 두 번 → 1회 삽입
- tracker.RemoteMonitor + Tracker._step_remote: 가짜 fetch 로 established→remote_on, closed→remote_off,
  닫힘 누락 3시간 캡
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
import remote_jump  # noqa: E402
import tracker  # noqa: E402

XML_TMPL = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
    "<Provider Name='Jump Desktop Connect'/><EventID Qualifiers='24576'>0</EventID><Version>0</Version>"
    "<Level>4</Level><Task>0</Task><Opcode>0</Opcode><Keywords>0x80000000000000</Keywords>"
    "<TimeCreated SystemTime='{time}'/><EventRecordID>{rid}</EventRecordID><Correlation/>"
    "<Execution ProcessID='4400' ThreadID='0'/><Channel>Application</Channel>"
    "<Computer>DESKTOP-B0HBMGL</Computer><Security/></System>"
    "<EventData><Data>{data}</Data></EventData></Event>"
)

PEER_A = "CD-01K2MMCE9DGY5T7BK69B09GQPF"
PEER_B = "CD-OTHERPEER000000000000000"


def utc_iso(ts, frac="0000000"):
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "." + frac + "Z"


def make_xml(ts, rid, data):
    return XML_TMPL.format(time=utc_iso(ts), rid=rid, data=data)


def xml_request(ts, rid, conn, peer=PEER_A, ip="222.106.87.58"):
    return make_xml(ts, rid, "Incomming Connection Request: Type:Cloud, ConnectionID:%s, PeerID:%s, "
                             "Email:x@y.z, IP:%s" % (conn, peer, ip))


def xml_established(ts, rid, conn, peer=PEER_A):
    return make_xml(ts, rid, "Incomming Connection Established: Type:Cloud, ConnectionID:%s, "
                             "TunnelID:rtc, PeerID:%s, Email:x@y.z" % (conn, peer))


def xml_auth(ts, rid, conn):
    return make_xml(ts, rid, "Authentication Succeeded: ConnectionID:%s, Type:Local, LocalUser:PC1" % conn)


def xml_closed(ts, rid, conn, peer=PEER_A):
    return make_xml(ts, rid, "Connection closed: ConnectionID:%s, PeerID:%s" % (conn, peer))


def ev(kind, ts, conn, peer=PEER_A, rid=0):
    return {"ts": float(ts), "record_id": rid, "kind": kind, "conn": conn, "peer": peer, "ip": None}


H = 3600.0
T0 = 1_800_000_000.0   # 임의의 기준 epoch


# ---------------------------------------------------------------- 파싱
class ParseTest(unittest.TestCase):
    def test_real_sample_closed_utc_to_local(self):
        xml = (
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
            "<Provider Name='Jump Desktop Connect'/><EventID Qualifiers='24576'>0</EventID><Version>0</Version>"
            "<Level>4</Level><Task>0</Task><Opcode>0</Opcode><Keywords>0x80000000000000</Keywords>"
            "<TimeCreated SystemTime='2026-09-02T16:22:29.2848908Z'/><EventRecordID>534630</EventRecordID>"
            "<Correlation/><Execution ProcessID='4400' ThreadID='0'/><Channel>Application</Channel>"
            "<Computer>DESKTOP-B0HBMGL</Computer><Security/></System><EventData>"
            "<Data>Connection closed: ConnectionID:ci5pucp6, PeerID:CD-01K2MMCE9DGY5T7BK69B09GQPF</Data>"
            "</EventData></Event>")
        r = remote_jump.parse_jump_event(xml)
        self.assertIsNotNone(r)
        self.assertEqual(r["kind"], "closed")
        self.assertEqual(r["conn"], "ci5pucp6")
        self.assertEqual(r["peer"], "CD-01K2MMCE9DGY5T7BK69B09GQPF")
        self.assertEqual(r["record_id"], 534630)
        self.assertIsNone(r["ip"])
        # UTC 16:22:29.284890 → epoch (로컬 시간대와 무관하게 UTC 기준 epoch 이어야 함)
        want = datetime.datetime(2026, 9, 2, 16, 22, 29, 284890,
                                 tzinfo=datetime.timezone.utc).timestamp()
        self.assertAlmostEqual(r["ts"], want, places=3)
        # 이 PC(UTC+9) 기준 로컬 표시는 09-03 01:22:29 — 시간대 오프셋만큼 어긋나지 않는지
        local = datetime.datetime.fromtimestamp(r["ts"])
        utc = datetime.datetime.utcfromtimestamp(r["ts"])
        self.assertEqual((utc.hour, utc.minute, utc.second), (16, 22, 29))
        self.assertEqual(local.minute, 22)

    def test_four_kinds(self):
        rq = remote_jump.parse_jump_event(xml_request(T0, 1, "abc123"))
        es = remote_jump.parse_jump_event(xml_established(T0 + 1, 2, "abc123"))
        au = remote_jump.parse_jump_event(xml_auth(T0 + 2, 3, "abc123"))
        cl = remote_jump.parse_jump_event(xml_closed(T0 + 3, 4, "abc123"))
        self.assertEqual([x["kind"] for x in (rq, es, au, cl)],
                         ["request", "established", "auth", "closed"])
        self.assertEqual([x["record_id"] for x in (rq, es, au, cl)], [1, 2, 3, 4])
        self.assertEqual(rq["ip"], "222.106.87.58")
        self.assertEqual(rq["peer"], PEER_A)
        self.assertEqual(es["peer"], PEER_A)
        self.assertIsNone(au["peer"])
        self.assertIsNone(au["ip"])
        self.assertAlmostEqual(rq["ts"], T0, places=3)
        self.assertAlmostEqual(cl["ts"], T0 + 3, places=3)

    def test_garbage_and_unrelated(self):
        self.assertIsNone(remote_jump.parse_jump_event(""))
        self.assertIsNone(remote_jump.parse_jump_event("<not xml"))
        self.assertIsNone(remote_jump.parse_jump_event(make_xml(T0, 9, "Service started")))
        self.assertIsNone(remote_jump.parse_jump_event(make_xml(T0, 9, "Connection closed: no id here")))

    def test_seven_digit_fraction(self):
        ts = remote_jump.parse_system_time("2026-09-02T16:22:29.2848908Z")
        self.assertAlmostEqual(ts % 1, 0.284890, places=5)
        ts2 = remote_jump.parse_system_time("2026-09-02T16:22:29Z")
        self.assertEqual(ts2 % 1, 0.0)
        self.assertIsNone(remote_jump.parse_system_time("garbage"))


# ---------------------------------------------------------------- 구간 계산
class IntervalTest(unittest.TestCase):
    def test_normal_pair(self):
        evs = [ev("request", T0 - 5, "c1"), ev("established", T0, "c1"), ev("auth", T0 + 1, "c1"),
               ev("closed", T0 + 600, "c1")]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 600, "c1")])

    def test_missing_closed_next_established_same_peer(self):
        evs = [ev("established", T0, "c1"), ev("established", T0 + 1 * H, "c2"),
               ev("closed", T0 + 2 * H, "c2")]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 1 * H, "c1"), (T0 + 1 * H, T0 + 2 * H, "c2")])

    def test_missing_closed_other_peer_not_used(self):
        evs = [ev("established", T0, "c1", peer=PEER_A), ev("established", T0 + 1 * H, "c2", peer=PEER_B),
               ev("closed", T0 + 2 * H, "c2", peer=PEER_B)]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 3 * H, "c1"), (T0 + 1 * H, T0 + 2 * H, "c2")])

    def test_max_session_hours_cap(self):
        evs = [ev("established", T0, "c1")]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 3 * H, "c1")])
        # 캡이 다음 established 보다 이르면 캡
        evs2 = [ev("established", T0, "c1"), ev("established", T0 + 5 * H, "c2")]
        iv2 = remote_jump.build_intervals(evs2, 3, T0 + 10 * H)
        self.assertEqual(iv2[0], (T0, T0 + 3 * H, "c1"))

    def test_in_progress_none(self):
        evs = [ev("established", T0, "c1")]
        iv = remote_jump.build_intervals(evs, 3, T0 + 1 * H)
        self.assertEqual(iv, [(T0, None, "c1")])
        self.assertEqual(remote_jump.open_connections(evs, 3, T0 + 1 * H), {"c1": T0})
        self.assertEqual(remote_jump.open_connections(evs, 3, T0 + 4 * H), {})

    def test_duplicate_established(self):
        evs = [ev("established", T0, "c1", rid=1), ev("established", T0 + 30, "c1", rid=2),
               ev("closed", T0 + 300, "c1", rid=3)]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 300, "c1")])

    def test_unsorted_input_and_reverse_order(self):
        evs = [ev("closed", T0 + 300, "c1"), ev("established", T0, "c1")]
        iv = remote_jump.build_intervals(evs, 3, T0 + 10 * H)
        self.assertEqual(iv, [(T0, T0 + 300, "c1")])


# ---------------------------------------------------------------- DB 를 쓰는 테스트 공통
class DBTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maeng_remote_")
        self.db = os.path.join(self.tmp, "t.db")
        self._old_env = os.environ.get("MAENG_WORKLOG_DB")
        os.environ["MAENG_WORKLOG_DB"] = self.db
        self.cfg = copy.deepcopy(common.DEFAULT_CONFIG)
        self.conn = common.connect(self.cfg, self.db)
        self._old_now = common.now_ts
        self.now = T0
        common.now_ts = lambda: self.now

    def tearDown(self):
        common.now_ts = self._old_now
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

    def events(self, kind=None):
        if kind:
            rows = self.conn.execute("SELECT ts, kind, extra FROM events WHERE kind=? ORDER BY ts, id",
                                     (kind,)).fetchall()
        else:
            rows = self.conn.execute("SELECT ts, kind, extra FROM events ORDER BY ts, id").fetchall()
        return [(r["ts"], r["kind"], json.loads(r["extra"]) if r["extra"] else None) for r in rows]


# ---------------------------------------------------------------- 백필
class BackfillTest(DBTestBase):
    def test_backfill_inserts_once(self):
        xmls = [
            xml_closed(T0 + 600, 4, "c1"),
            xml_auth(T0 + 2, 3, "c1"),
            xml_established(T0 + 1, 2, "c1"),
            xml_request(T0, 1, "c1"),
            xml_established(T0 + 2 * H, 5, "c2"),   # closed 누락 → 3h 캡
        ]
        calls = []

        def fetch(window_ms):
            calls.append(window_ms)
            return list(xmls)

        now = T0 + 10 * H
        r1 = tracker.backfill_jump(self.conn, self.cfg, now, fetch_xml_fn=fetch)
        self.assertEqual(r1["intervals"], 2)
        self.assertEqual(r1["inserted"], 4)
        self.assertEqual(calls[0], 7 * 86400 * 1000)
        ons = self.events("remote_on")
        offs = self.events("remote_off")
        self.assertEqual(len(ons), 2)
        self.assertEqual(len(offs), 2)
        self.assertAlmostEqual(ons[0][0], T0 + 1, places=3)
        self.assertAlmostEqual(offs[0][0], T0 + 600, places=3)
        self.assertAlmostEqual(ons[1][0], T0 + 2 * H, places=3)
        self.assertAlmostEqual(offs[1][0], T0 + 5 * H, places=3)
        self.assertEqual(ons[0][2], {"source": "jump", "conn": "c1", "backfill": True})
        # 저장 형식이 LIKE 패턴('"conn": "c1"')과 맞는지 원문으로 확인
        raw = self.conn.execute("SELECT extra FROM events WHERE kind='remote_on' ORDER BY id").fetchone()[0]
        self.assertIn('"conn": "c1"', raw)

        # 같은 conn 두 번 → 늘지 않음
        r2 = tracker.backfill_jump(self.conn, self.cfg, now, fetch_xml_fn=fetch)
        self.assertEqual(r2["inserted"], 0)
        self.assertEqual(r2["skipped"], 4)
        self.assertEqual(len(self.events("remote_on")), 2)
        self.assertEqual(len(self.events("remote_off")), 2)

    def test_backfill_in_progress_only_on(self):
        xmls = [xml_established(T0, 2, "c1")]
        r = tracker.backfill_jump(self.conn, self.cfg, T0 + 600, fetch_xml_fn=lambda w: xmls)
        self.assertEqual(r["inserted"], 1)
        self.assertEqual(len(self.events("remote_on")), 1)
        self.assertEqual(len(self.events("remote_off")), 0)
        # 시간이 지나 캡에 걸리면 off 만 추가
        r = tracker.backfill_jump(self.conn, self.cfg, T0 + 4 * H, fetch_xml_fn=lambda w: xmls)
        self.assertEqual(r["inserted"], 1)
        self.assertEqual(len(self.events("remote_on")), 1)
        self.assertEqual(len(self.events("remote_off")), 1)

    def test_backfill_fetch_failure(self):
        def bad(w):
            raise RuntimeError("no eventlog")
        r = tracker.backfill_jump(self.conn, self.cfg, T0, fetch_xml_fn=bad)
        self.assertTrue(r["disabled"])
        self.assertEqual(self.events(), [])

    def test_backfill_disabled_by_config(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["remote_detect"]["jump_eventlog"] = False
        called = []
        r = tracker.backfill_jump(self.conn, cfg, T0, fetch_xml_fn=lambda w: called.append(w) or [])
        self.assertTrue(r["disabled"])
        self.assertEqual(called, [])


# ---------------------------------------------------------------- RemoteMonitor + Tracker 전이
class FakeLog(object):
    """가짜 이벤트 로그: 시각 기준으로 window 안의 XML 만 돌려준다(최신순)."""

    def __init__(self, get_now):
        self.items = []     # (ts, xml)
        self.get_now = get_now
        self.calls = 0

    def add(self, ts, xml):
        self.items.append((ts, xml))

    def __call__(self, window_ms):
        self.calls += 1
        now = self.get_now()
        lo = now - window_ms / 1000.0
        out = [x for ts, x in self.items if lo <= ts <= now]
        out.sort(key=lambda x: x, reverse=True)
        return out


class MonitorTest(DBTestBase):
    def make(self, rdp=False):
        self.rdp = rdp
        self.log = FakeLog(lambda: self.now)
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=self.log, rdp_fn=lambda: self.rdp)
        tr = tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon,
                             enable_self_update=False, enable_server_watch=False)
        return mon, tr

    def test_established_on_closed_off(self):
        mon, tr = self.make()
        mon.restore(self.now)
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        self.assertEqual(self.events(), [])

        # established (poll 창 20초 안에 들어오게 now 보다 3초 전에 기록)
        self.now += 10
        self.log.add(self.now - 3, xml_established(self.now - 3, 10, "c1"))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        evs = self.events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0][1], "remote_on")
        self.assertAlmostEqual(evs[0][0], self.now, places=3)
        self.assertEqual(evs[0][2], {"source": "jump", "conn": "c1"})

        # 같은 이벤트가 다음 폴링 창에 또 보여도 중복 처리 없음
        self.now += 5
        tr._step_remote(self.now)
        self.assertEqual(len(self.events()), 1)

        # closed → remote_off
        self.now += 600
        self.log.add(self.now - 2, xml_closed(self.now - 2, 11, "c1"))
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "remote_off"])
        self.assertEqual(evs[1][2], {"source": "jump", "conn": "c1"})
        self.assertEqual(mon.open, {})

    def test_poll_interval_respected(self):
        mon, tr = self.make()
        mon.restore(self.now)
        n0 = self.log.calls
        tr._step_remote(self.now + 1)
        tr._step_remote(self.now + 2)
        self.assertEqual(self.log.calls, n0)      # poll_sec(5) 안 지남
        tr._step_remote(self.now + 6)
        self.assertEqual(self.log.calls, n0 + 1)

    def test_missing_closed_cap_3h(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 20, "c9"))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        t_on = self.now
        # 2시간 59분: 아직 원격
        self.now = t_on + 3 * H - 60
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        # 3시간 경과(닫힘 없음): 자동 제거 → remote_off
        self.now = t_on + 3 * H + 5
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "remote_off"])
        self.assertAlmostEqual(evs[1][0], self.now, places=3)
        self.assertEqual(mon.open, {})

    def test_restore_open_at_start(self):
        # 시작 30분 전에 established, closed 없음 → 시작 시 원격으로 복원
        self.log.add if hasattr(self, "log") else None
        mon, tr = self.make()
        self.log.add(self.now - 1800, xml_established(self.now - 1800, 30, "c5"))
        opened = mon.restore(self.now)
        self.assertEqual(list(opened), ["c5"])
        rm, extra = mon.check(self.now)
        self.assertTrue(rm)
        self.assertEqual(extra, {"source": "jump", "conn": "c5"})
        # 4시간 전 것은 캡에 걸려 복원되지 않음
        mon2, tr2 = self.make()
        self.log.add(self.now - 4 * H, xml_established(self.now - 4 * H, 31, "c6"))
        self.assertEqual(mon2.restore(self.now), {})

    def test_rdp_and_jump_overlap(self):
        mon, tr = self.make(rdp=True)
        mon.restore(self.now)
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        self.assertEqual(self.events()[0][2], {"source": "rdp", "conn": None})
        # jump 도 켜짐 → 전이 없음
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 40, "c7"))
        tr._step_remote(self.now)
        self.assertEqual(len(self.events()), 1)
        # rdp 꺼져도 jump 가 남아 있으면 계속 원격
        self.rdp = False
        self.now += 10
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        self.assertEqual(len(self.events()), 1)
        # jump 닫힘 → 마지막 꺼진 쪽(jump) 이 끔
        self.now += 10
        self.log.add(self.now - 1, xml_closed(self.now - 1, 41, "c7"))
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        self.assertEqual(self.events()[1][2], {"source": "jump", "conn": "c7"})

    def test_eventlog_failure_disables_jump_only(self):
        self.rdp = False

        def bad(w):
            raise RuntimeError("EvtQuery denied")
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=bad, rdp_fn=lambda: self.rdp)
        mon.restore(self.now)
        self.assertFalse(mon.jump_enabled)
        self.assertEqual(mon.check(self.now), (False, {"source": "rdp", "conn": None}))
        self.rdp = True
        self.assertEqual(mon.check(self.now + 10)[0], True)

    def test_seen_bounded(self):
        mon, tr = self.make()
        mon.restore(self.now)
        for i in range(700):
            self.now += 5
            self.log.add(self.now - 1, xml_auth(self.now - 1, 1000 + i, "cx"))
            mon.jump_poll(self.now)
        self.assertLessEqual(len(mon.seen), tracker.SEEN_RECORDS_MAX)


# ---------------------------------------------------------------- 자가 재시작 / 서버 감시 (가짜 spawn)
class FakeProc(object):
    def __init__(self, alive, rc=1):
        self.alive = alive
        self.returncode = None if alive else rc
        self.pid = 4242

    def poll(self):
        return None if self.alive else self.returncode


class SelfUpdateTest(DBTestBase):
    def make(self):
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=lambda w: [], rdp_fn=lambda: False)
        return tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon,
                               enable_self_update=True, enable_server_watch=True)

    def test_no_change_no_restart(self):
        tr = self.make()
        self.assertFalse(tr._check_self_update(spawn_fn=lambda a: FakeProc(True), grace_sec=0))
        self.assertTrue(tr.running)

    def test_change_child_alive_restarts(self):
        tr = self.make()
        tr.code_mtimes0["tracker.py"] -= 100   # 시작 후 파일이 바뀐 것처럼
        spawned = []
        ok = tr._check_self_update(spawn_fn=lambda a: spawned.append(a) or FakeProc(True),
                                   compiles_fn=lambda: True, grace_sec=0.1)
        self.assertTrue(ok)
        self.assertEqual(spawned, [[]])
        self.assertFalse(tr.running)
        self.assertTrue(tr.restart_requested)

    def test_change_child_dies_keeps_running(self):
        tr = self.make()
        tr.code_mtimes0["common.py"] -= 100
        ok = tr._check_self_update(spawn_fn=lambda a: FakeProc(False), compiles_fn=lambda: True,
                                   grace_sec=0.1)
        self.assertFalse(ok)
        self.assertTrue(tr.running)
        # 같은 변경으로 다시 spawn 하지 않는다
        self.assertFalse(tr._check_self_update(spawn_fn=lambda a: FakeProc(True),
                                               compiles_fn=lambda: True, grace_sec=0))

    def test_compile_failure_holds(self):
        tr = self.make()
        tr.code_mtimes0["remote_jump.py"] -= 100
        spawned = []
        ok = tr._check_self_update(spawn_fn=lambda a: spawned.append(a) or FakeProc(True),
                                   compiles_fn=lambda: False, grace_sec=0)
        self.assertFalse(ok)
        self.assertEqual(spawned, [])
        self.assertTrue(tr.running)
        # 다음 검사 때 컴파일이 되면 재시작
        ok = tr._check_self_update(spawn_fn=lambda a: spawned.append(a) or FakeProc(True),
                                   compiles_fn=lambda: True, grace_sec=0)
        self.assertTrue(ok)

    def test_real_code_compiles(self):
        self.assertTrue(tracker.code_compiles())

    def test_server_watch_spawns_once_after_30s(self):
        tr = self.make()
        spawned = []
        import time as _t
        real_mono = _t.monotonic
        base = [1000.0]
        _t.monotonic = lambda: base[0]
        try:
            down = lambda p: False
            up = lambda p: True
            self.assertFalse(tr._watch_server(self.now, port_open_fn=down, spawn_fn=lambda: spawned.append(1)))
            base[0] += 29
            self.assertFalse(tr._watch_server(self.now, port_open_fn=down, spawn_fn=lambda: spawned.append(1)))
            base[0] += 2
            self.assertTrue(tr._watch_server(self.now, port_open_fn=down, spawn_fn=lambda: spawned.append(1)))
            self.assertEqual(spawned, [1])
            base[0] += 30
            self.assertFalse(tr._watch_server(self.now, port_open_fn=down, spawn_fn=lambda: spawned.append(1)))
            self.assertEqual(spawned, [1])      # 60초 안엔 재시도 금지
            base[0] += 31
            self.assertTrue(tr._watch_server(self.now, port_open_fn=down, spawn_fn=lambda: spawned.append(1)))
            self.assertEqual(spawned, [1, 1])
            # 살아나면 리셋
            self.assertFalse(tr._watch_server(self.now, port_open_fn=up, spawn_fn=lambda: spawned.append(1)))
            self.assertIsNone(tr.server_down_since)
        finally:
            _t.monotonic = real_mono

    def test_debug_seconds_disables(self):
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=lambda w: [], rdp_fn=lambda: False)
        tr = tracker.Tracker(self.cfg, self.conn, self.tmp, seconds=5, monitor=mon)
        self.assertFalse(tr.self_update_enabled)
        self.assertFalse(tr.server_watch_enabled)
        tr2 = tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon)
        self.assertTrue(tr2.self_update_enabled)
        # 테스트 DB(MAENG_WORKLOG_DB)로 도는 트래커는 서버 감시를 끈다(실제 포트에 테스트 DB 서버를 띄우지 않게)
        self.assertFalse(tr2.server_watch_enabled)
        os.environ.pop("MAENG_WORKLOG_DB", None)
        try:
            tr3 = tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon)
            self.assertTrue(tr3.server_watch_enabled)
        finally:
            os.environ["MAENG_WORKLOG_DB"] = self.db
        self.assertNotIn("MAENG_WORKLOG_DB", tracker.server_env())

    def test_handoff_child_ready(self):
        calls = []
        ok = tracker.handoff_to_child(
            FakeProc(True), wait_sec=2, release_fn=lambda: calls.append("release"),
            wait_ready_fn=lambda t: True, acquire_fn=lambda retry_sec: calls.append("acquire") or True,
            kill_fn=lambda p: calls.append("kill"), poll_sec=0.01)
        self.assertTrue(ok)
        self.assertEqual(calls, ["release"])       # 준비됨 → 뮤텍스 되찾지 않음

    def test_handoff_child_dies_before_ready(self):
        # 자식이 뮤텍스를 얻은 뒤 start() 에서 죽는 경우(NameError 등): 준비 신호 없이 종료 → 부모가 계속
        calls = []
        proc = FakeProc(True)
        ticks = {"n": 0}

        def wait_ready(t):
            ticks["n"] += 1
            if ticks["n"] >= 3:
                proc.alive = False
                proc.returncode = 1
            return False
        ok = tracker.handoff_to_child(
            proc, wait_sec=5, release_fn=lambda: calls.append("release"), wait_ready_fn=wait_ready,
            acquire_fn=lambda retry_sec: calls.append("acquire") or True,
            kill_fn=lambda p: calls.append("kill"), poll_sec=0.01)
        self.assertFalse(ok)
        self.assertEqual(calls, ["release", "acquire"])

    def test_handoff_timeout_kills_child(self):
        calls = []
        proc = FakeProc(True)

        def kill(p):
            calls.append("kill")
            p.alive = False
            p.returncode = -9
        ok = tracker.handoff_to_child(
            proc, wait_sec=0.05, release_fn=lambda: calls.append("release"), wait_ready_fn=lambda t: False,
            acquire_fn=lambda retry_sec: calls.append("acquire") or True, kill_fn=kill, poll_sec=0.01)
        self.assertFalse(ok)
        self.assertEqual(calls, ["release", "kill", "acquire"])

    def test_handoff_mutex_taken_by_other(self):
        ok = tracker.handoff_to_child(
            FakeProc(False), wait_sec=1, release_fn=lambda: None, wait_ready_fn=lambda t: False,
            acquire_fn=lambda retry_sec: False, kill_fn=lambda p: None, poll_sec=0.01)
        self.assertTrue(ok)     # 다른 수집기가 뮤텍스를 잡음 → 종료

    def test_check_self_update_keeps_proc_for_handoff(self):
        tr = self.make()
        tr.code_mtimes0["tracker.py"] -= 100
        resets = []
        ok = tr._check_self_update(spawn_fn=lambda a: FakeProc(True), compiles_fn=lambda: True,
                                   grace_sec=0, reset_ready_fn=lambda: resets.append(1))
        self.assertTrue(ok)
        self.assertEqual(resets, [1])
        self.assertIsNotNone(tr.restart_proc)
        self.assertGreaterEqual(tracker.MUTEX_RETRY_SEC, 30)

    def test_ready_event_roundtrip(self):
        # 실제 named event: 부모 reset → (신호 없음) → 자식 signal → 부모 wait True
        tracker.reset_ready_event()
        self.assertFalse(tracker.wait_ready_event(0))
        tracker.signal_ready()
        self.assertTrue(tracker.wait_ready_event(0))
        tracker.reset_ready_event()
        self.assertFalse(tracker.wait_ready_event(0))


# ---------------------------------------------------------------- 폴링 공백 / 일시정지 / 부팅 / 캡 보정 / 오류 재시도
class MonitorGapTest(DBTestBase):
    def make(self, rdp=False, boot_ts=None):
        self.rdp = rdp
        self.log = FakeLog(lambda: self.now)
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=self.log, rdp_fn=lambda: self.rdp, boot_ts=boot_ts)
        tr = tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon,
                             enable_self_update=False, enable_server_watch=False)
        return mon, tr

    def _observe(self, tr, n=1, step=2):
        for _ in range(n):
            self.now += step
            # 잠금/idle/전경 창은 실제 Win32 를 부르므로 원격 단계까지만 흉내낸다
            until = tr.read_paused_until()
            paused_now = until is not None and self.now < until
            if paused_now and not tr.paused:
                tr.rec.event("pause", ts=self.now, extra={"until": until})
                tr.paused = True
            elif not paused_now and tr.paused:
                tr.rec.event("resume", ts=self.now)
                tr.paused = False
            if tr.paused:
                tr.monitor.jump_poll(self.now)
                tr._apply_late_closed()
                continue
            tr._step_remote(self.now)

    def test_paused_established_seen_after_resume(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self._observe(tr)
        common.set_state(self.conn, "paused_until", self.now + 3600)
        self._observe(tr)
        self.assertTrue(tr.paused)
        self.now += 600
        self.log.add(self.now, xml_established(self.now, 1, "cA"))
        self._observe(tr, n=5)
        self.assertEqual(list(mon.open), ["cA"])      # 일시정지 중에도 상태는 최신
        self.assertFalse(tr.remote)                    # 기록은 해제 뒤
        self.now += 3600
        common.set_state(self.conn, "paused_until", None)
        self._observe(tr, n=2)
        self.assertTrue(tr.remote)
        kinds = [e[1] for e in self.events()]
        self.assertEqual(kinds, ["pause", "resume", "remote_on"])

    def test_paused_closed_seen_after_resume(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "cB"))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        common.set_state(self.conn, "paused_until", self.now + 1800)
        self._observe(tr)
        self.now += 60
        self.log.add(self.now, xml_closed(self.now, 2, "cB"))
        self._observe(tr, n=3)
        self.assertEqual(mon.open, {})
        self.now += 1800
        common.set_state(self.conn, "paused_until", None)
        self._observe(tr, n=2)
        self.assertFalse(tr.remote)
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "pause", "resume", "remote_off"])
        self.assertNotIn("cap", evs[-1][2])

    def test_loop_gap_widens_window(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 6
        tr._step_remote(self.now)
        self.now += 3
        self.log.add(self.now, xml_established(self.now, 1, "cC"))
        self.now += 25                                  # 25초 만에 다음 루프(기본 창 20초 밖)
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        self.assertEqual(list(mon.open), ["cC"])
        # 긴 공백(절전 8h) 뒤: 그 사이에 열리고 닫힌 것은 전이 없이 정리, 창은 24h 상한
        self.now += 3600
        self.log.add(self.now, xml_closed(self.now, 2, "cC"))
        self.now += 8 * H
        self.log.add(self.now - 7 * H, xml_established(self.now - 7 * H, 3, "cD"))
        self.log.add(self.now - 6 * H, xml_closed(self.now - 6 * H, 4, "cD"))
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        self.assertEqual(mon.open, {})
        self.assertEqual([e[1] for e in self.events()], ["remote_on", "remote_off"])
        self.assertLessEqual(mon._poll_window_sec(self.now + 3 * 86400), tracker.POLL_WINDOW_MAX_SEC)

    def test_window_based_on_last_success(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.assertAlmostEqual(mon._poll_window_sec(self.now + 5), 20.0)
        self.assertAlmostEqual(mon._poll_window_sec(self.now + 100), 105.0)

    def test_same_peer_reconnect_closes_previous(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "k1", PEER_A))
        tr._step_remote(self.now)
        self.now += H       # closed 누락 후 같은 peer 재접속
        self.log.add(self.now - 1, xml_established(self.now - 1, 2, "k2", PEER_A))
        tr._step_remote(self.now)
        self.assertEqual(list(mon.open), ["k2"])
        self.assertTrue(tr.remote)
        self.now += 600
        self.log.add(self.now - 1, xml_closed(self.now - 1, 3, "k2", PEER_A))
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        self.assertEqual(mon.open, {})
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "remote_off"])
        self.assertEqual(evs[1][2]["conn"], "k2")
        # 다른 peer 는 닫지 않는다
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 4, "k3", PEER_A))
        self.log.add(self.now - 1, xml_established(self.now - 1, 5, "k4", PEER_B))
        tr._step_remote(self.now)
        self.assertEqual(sorted(mon.open), ["k3", "k4"])

    def test_restore_seen_keeps_newest(self):
        mon, tr = self.make()
        for i in range(600):
            self.log.add(self.now - 3600 + i, xml_auth(self.now - 3600 + i, 10000 + i, "z"))
        mon.restore(self.now)
        self.assertEqual(len(mon.seen), tracker.SEEN_RECORDS_MAX)
        self.assertEqual(max(mon.seen), 10599)
        self.assertEqual(min(mon.seen), 10100)

    def test_boot_ts_closes_pre_boot_conn(self):
        boot = self.now - 600
        mon, tr = self.make(boot_ts=boot)
        self.log.add(self.now - 1800, xml_established(self.now - 1800, 30, "cb"))   # 부팅 전, closed 없음
        self.assertEqual(mon.restore(self.now), {})
        self.assertFalse(mon.check(self.now)[0])
        # 폴링에서 부팅 이전 established 가 보여도 무시
        self.log.add(self.now - 610, xml_established(self.now - 610, 31, "cb2", PEER_B))
        mon.jump_poll(self.now, force=True)
        self.assertEqual(mon.open, {})
        # 백필은 부팅 시각에 닫는다
        evs = tracker.parse_jump_xml_list(self.log(86400 * 1000))
        iv = remote_jump.build_intervals(evs, 3, self.now, boot)
        self.assertEqual([(round(a - T0), round(b - T0), c) for a, b, c in iv],
                         [(-1800, -600, "cb"), (-610, -600, "cb2")])
        r = tracker.backfill_jump(self.conn, self.cfg, self.now, fetch_xml_fn=self.log, boot_ts=boot)
        self.assertEqual(r["inserted"], 4)
        offs = [e for e in self.events("remote_off")]
        self.assertTrue(all(abs(e[0] - boot) < 1e-6 for e in offs))
        # 부팅 뒤 established 는 영향 없음
        mon2, _ = self.make(boot_ts=boot)
        self.log.add(self.now - 300, xml_established(self.now - 300, 32, "cc"))
        self.assertEqual(list(mon2.restore(self.now)), ["cc"])

    def test_cap_then_late_closed_fixes_off(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 20, "c9"))
        tr._step_remote(self.now)
        t_on = self.now
        self.now = t_on + 3 * H + 5
        tr._step_remote(self.now)
        self.assertFalse(tr.remote)
        evs = self.events()
        self.assertEqual(evs[1][2], {"source": "jump", "conn": "c9", "cap": True})
        t_cap = evs[1][0]
        # 1시간 뒤 실제 closed 도착 → remote_off 시각 보정
        self.now += H
        self.log.add(self.now - 1, xml_closed(self.now - 1, 21, "c9"))
        tr._step_remote(self.now)
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "remote_off"])
        self.assertAlmostEqual(evs[1][0], self.now - 1, places=3)
        self.assertFalse(evs[1][2]["cap"])
        self.assertAlmostEqual(evs[1][2]["cap_ts"], t_cap, places=3)
        self.assertTrue(evs[1][2]["late_closed"])
        self.assertNotIn("c9", mon.capped)
        # 재시작 백필도 이미 있는 off 를 늘리지 않고 그대로 둔다(보정 완료)
        r = tracker.backfill_jump(self.conn, self.cfg, self.now + 60, fetch_xml_fn=self.log)
        self.assertEqual((r["inserted"], r["fixed"]), (0, 0))

    def test_cap_late_closed_not_fixed_over_other_remote_on(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 20, "c9"))
        tr._step_remote(self.now)
        self.now += 3 * H + 5
        tr._step_remote(self.now)          # 캡 off
        self.now += 600
        self.rdp = True
        tr._step_remote(self.now)          # rdp on
        self.now += 600
        self.rdp = False
        tr._step_remote(self.now)          # rdp off
        self.now += 600
        self.log.add(self.now - 1, xml_closed(self.now - 1, 21, "c9"))
        tr._step_remote(self.now)
        evs = self.events()
        self.assertEqual([e[1] for e in evs], ["remote_on", "remote_off", "remote_on", "remote_off"])
        self.assertTrue(evs[1][2]["cap"])   # 사이에 다른 remote_on → 보정 안 함

    def test_backfill_fixes_capped_off_after_restart(self):
        # 라이브: on, 캡 off(cap:true). 트래커 재시작 뒤 closed 가 로그에 있음 → 백필이 off 시각을 보정
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 20, "c9"))
        tr._step_remote(self.now)
        self.now += 3 * H + 5
        tr._step_remote(self.now)
        self.now += H
        self.log.add(self.now - 1, xml_closed(self.now - 1, 21, "c9"))
        r = tracker.backfill_jump(self.conn, self.cfg, self.now, fetch_xml_fn=self.log)
        self.assertEqual((r["inserted"], r["fixed"]), (0, 1))
        evs = self.events()
        self.assertAlmostEqual(evs[1][0], self.now - 1, places=3)

    def _err(self, code):
        import pywintypes
        return pywintypes.error(code, "EvtQuery", "boom")

    def test_transient_error_retries_then_suspends(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "c1"))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        good = self.log
        fails = {"n": 0}

        def flaky(w):
            fails["n"] += 1
            raise self._err(1722)
        mon._fetch = flaky
        for _ in range(2):
            self.now += 5
            tr._step_remote(self.now)
        self.assertTrue(mon.jump_enabled)
        self.assertTrue(tr.remote)                      # 2회 실패까지는 open 유지
        self.assertEqual(list(mon.open), ["c1"])
        self.now += 5
        tr._step_remote(self.now)                       # 3회째 → 중단
        self.assertTrue(mon.jump_enabled)
        self.assertIsNotNone(mon.suspended_until)
        self.assertFalse(tr.remote)
        n = fails["n"]
        self.now += 60
        tr._step_remote(self.now)
        self.assertEqual(fails["n"], n)                 # 중단 중엔 읽지 않음
        # 복구: 10분 뒤 restore 로 open 재구성 → 아직 연결 중이면 다시 remote_on
        mon._fetch = good
        self.now += tracker.JUMP_RETRY_SEC
        tr._step_remote(self.now)
        self.assertIsNone(mon.suspended_until)
        self.assertEqual(list(mon.open), ["c1"])
        self.assertTrue(tr.remote)
        self.assertEqual([e[1] for e in self.events()], ["remote_on", "remote_off", "remote_on"])

    def test_transient_error_recovers_before_limit(self):
        mon, tr = self.make()
        mon.restore(self.now)
        good = self.log
        mon._fetch = lambda w: (_ for _ in ()).throw(self._err(1460))
        self.now += 6
        tr._step_remote(self.now)
        self.assertEqual(mon.fail_count, 1)
        mon._fetch = good
        self.now += 5
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "c1"))
        tr._step_remote(self.now)
        self.assertEqual(mon.fail_count, 0)
        self.assertTrue(tr.remote)

    def test_permanent_error_disables(self):
        for code in (5, 15007, 15001):
            mon, tr = self.make()
            mon._fetch = lambda w, c=code: (_ for _ in ()).throw(self._err(c))
            mon.restore(self.now)
            self.assertFalse(mon.jump_enabled)
        self.assertFalse(tracker.is_transient_evt_error(RuntimeError("x")))
        self.assertTrue(tracker.is_transient_evt_error(self._err(1460)))


# ---------------------------------------------------------------- 백필: 겹침 / 존재 판정
class BackfillOverlapTest(DBTestBase):
    def make(self, rdp_holder=None):
        self.log = FakeLog(lambda: self.now)
        rdp_holder = rdp_holder if rdp_holder is not None else {"on": False}
        self.rdp_holder = rdp_holder
        mon = tracker.RemoteMonitor(self.cfg, fetch_xml_fn=self.log, rdp_fn=lambda: rdp_holder["on"])
        tr = tracker.Tracker(self.cfg, self.conn, self.tmp, monitor=mon,
                             enable_self_update=False, enable_server_watch=False)
        tr.rec.open_session(self.now)
        return mon, tr

    def _remote_timeline(self):
        rm = False
        out = []
        for ts, k, ex in self.events():
            if k == "remote_on":
                rm = True
            elif k == "remote_off":
                rm = False
            else:
                continue
            out.append((round(ts - T0), rm))
        return out

    def test_overlapping_conns_live_then_backfill(self):
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "c1", PEER_B))
        tr._step_remote(self.now)
        self.now += 600
        self.log.add(self.now - 1, xml_established(self.now - 1, 2, "c2", PEER_A))
        tr._step_remote(self.now)
        self.now += 600
        self.log.add(self.now - 1, xml_closed(self.now - 1, 3, "c1", PEER_B))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        self.now += 600
        self.log.add(self.now - 1, xml_closed(self.now - 1, 4, "c2", PEER_A))
        tr._step_remote(self.now)
        tr.rec.heartbeat(self.now)
        self.assertEqual(self._remote_timeline(), [(10, True), (1810, False)])
        r = tracker.backfill_jump(self.conn, self.cfg, self.now + 60, fetch_xml_fn=self.log)
        self.assertEqual(r["skipped_overlap"], 1)
        tl = self._remote_timeline()
        # off 가 1209 에 끼어들지 않는다: 켜짐 구간이 10~1810 그대로
        self.assertEqual([t for t in tl if not t[1]], [(1810, False)])
        self.assertEqual(tl[0], (10, True))
        # 두 번째 백필은 아무것도 넣지 않는다
        r2 = tracker.backfill_jump(self.conn, self.cfg, self.now + 120, fetch_xml_fn=self.log)
        self.assertEqual(r2["inserted"], 0)

    def test_jump_then_rdp_overlap_backfill(self):
        rdp = {"on": False}
        mon, tr = self.make(rdp)
        mon.restore(self.now)
        tr._step_remote(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "j1"))
        tr._step_remote(self.now)
        rdp["on"] = True
        self.now += 600
        tr._step_remote(self.now)
        self.now += 600
        self.log.add(self.now - 1, xml_closed(self.now - 1, 2, "j1"))
        tr._step_remote(self.now)
        self.assertTrue(tr.remote)
        self.now += 600
        rdp["on"] = False
        tr._step_remote(self.now)
        tr.rec.heartbeat(self.now)
        self.assertEqual(self._remote_timeline(), [(10, True), (1810, False)])
        r = tracker.backfill_jump(self.conn, self.cfg, self.now + 60, fetch_xml_fn=self.log)
        self.assertEqual(r["skipped_overlap"], 1)
        self.assertEqual(r["inserted"], 0)
        self.assertEqual(self._remote_timeline(), [(10, True), (1810, False)])

    def test_first_time_backfill_overlap_old_session(self):
        # 옛 코드 세션(라이브 remote 이벤트 없음) 동안 두 conn 이 겹침 → on(c1), on(c2), off(c2) 만
        mon, tr = self.make()
        self.log.add(self.now + 10, xml_established(self.now + 10, 1, "c1", PEER_B))
        self.log.add(self.now + 610, xml_established(self.now + 610, 2, "c2", PEER_A))
        self.log.add(self.now + 1210, xml_closed(self.now + 1210, 3, "c1", PEER_B))
        self.log.add(self.now + 1810, xml_closed(self.now + 1810, 4, "c2", PEER_A))
        self.now += 2000
        tr.rec.heartbeat(self.now)
        r = tracker.backfill_jump(self.conn, self.cfg, self.now, fetch_xml_fn=self.log)
        self.assertEqual((r["inserted"], r["skipped_overlap"]), (3, 1))
        self.assertEqual(self._remote_timeline(), [(10, True), (610, True), (1810, False)])

    def test_backfill_off_inserted_when_tracker_dead(self):
        # 라이브 on 뒤 트래커가 죽어(세션 커버리지 밖) closed 가 왔으면 off 를 넣는다
        mon, tr = self.make()
        mon.restore(self.now)
        self.now += 10
        self.log.add(self.now - 1, xml_established(self.now - 1, 1, "c1"))
        tr._step_remote(self.now)
        tr.rec.close_session(self.now + 100, "crash")
        self.log.add(self.now + 1200, xml_closed(self.now + 1200, 2, "c1"))
        self.now += 2000
        r = tracker.backfill_jump(self.conn, self.cfg, self.now, fetch_xml_fn=self.log)
        self.assertEqual(r["inserted"], 1)
        self.assertEqual(self._remote_timeline(), [(10, True), (1210, False)])

    def test_exists_exact_match(self):
        rec = tracker.Recorder(self.conn)
        rec.event("remote_on", ts=self.now, extra={"source": "jump", "conn": "AbCd_1"})
        self.assertTrue(tracker._remote_event_exists(self.conn, "remote_on", "AbCd_1"))
        self.assertFalse(tracker._remote_event_exists(self.conn, "remote_on", "abcd_1"))
        self.assertFalse(tracker._remote_event_exists(self.conn, "remote_on", "AbCdX1"))
        self.assertFalse(tracker._remote_event_exists(self.conn, "remote_on", "AbCd_"))
        self.assertFalse(tracker._remote_event_exists(self.conn, "remote_off", "AbCd_1"))


if __name__ == "__main__":
    unittest.main()
