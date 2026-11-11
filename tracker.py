# -*- coding: utf-8 -*-
"""
tracker.py — MAENG_worklog 상시 수집기 (에이전트 A)

pythonw 로 상시 실행. 전경 창 / 무입력(idle) / 잠금 / 원격 / 일시정지 상태를
events 테이블에 기록하고 sessions 에 heartbeat 를 남긴다.

    python tracker.py --seconds 40        # 40초 뒤 정상 종료(디버그, 자가 재시작·서버 감시 끔)
    python tracker.py --db PATH           # DB 경로 재지정(테스트)
    python tracker.py --backfill-only     # Jump Desktop 이벤트 로그 백필만 하고 종료(뮤텍스·세션 없음)

원격 감지(§13): RDP(SM_REMOTESESSION) + Jump Desktop Connect 이벤트 로그(Application 채널).
자가 재시작(§13.4): tracker.py / common.py / remote_jump.py 가 바뀌면 새 프로세스를 띄우고 종료.
서버 감시(§13.4): config.port 가 30초 이상 닫혀 있으면 pythonw server.py 를 1회 띄운다.

DESIGN.md §0, §3, §5, §6, §13 이 계약이다.
"""
import os
import sys
import json
import time
import ctypes
import signal
import socket
import sqlite3
import logging
import logging.handlers
import argparse
import datetime
import tempfile
import py_compile
import subprocess
import collections
from ctypes import wintypes

import win32api
import win32gui
import win32event
import win32process
try:
    import win32evtlog
    import pywintypes
except Exception:   # pragma: no cover — pywin32 에 evtlog 가 없는 환경
    win32evtlog = None
    pywintypes = None

import common
import remote_jump

# ---------------------------------------------------------------- 상수
MUTEX_NAME = "Local\\MAENG_worklog_tracker"
ERROR_ALREADY_EXISTS = 183
SM_REMOTESESSION = 0x1000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WTS_CURRENT_SERVER_HANDLE = None
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_SESSION_INFO_EX = 25
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1

DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
RESTART_ENV = "MAENG_WORKLOG_RESTARTED"           # 자가 재시작으로 뜬 자식 프로세스 표시(뮤텍스 재시도용)
READY_EVENT_NAME = "Local\\MAENG_worklog_tracker_ready"   # 자식이 start() 를 마치면 켜는 준비 신호
MUTEX_RETRY_SEC = 30.0                            # 자식이 뮤텍스를 기다리는 최대 시간(부모의 stop()·DB 잠김 여유)
MUTEX_RETRY_INTERVAL = 0.5
RESTART_CHILD_GRACE_SEC = 1.0                     # spawn 직후 import 오류 등으로 곧바로 죽는지만 본다(진짜 확인은 준비 신호)
RESTART_READY_WAIT_SEC = 60.0                     # 부모가 뮤텍스를 놓은 뒤 자식의 준비 신호를 기다리는 최대 시간
SERVER_DOWN_SEC = 30.0                            # 서버 포트가 이만큼 연속으로 닫혀 있으면 spawn
SERVER_SPAWN_COOLDOWN_SEC = 60.0                  # spawn 후 최소 이 시간은 재시도 금지
CODE_FILES = ("tracker.py", "common.py", "remote_jump.py")
EVT_CHANNEL = "Application"
EVT_NEXT_TIMEOUT_MS = 10000                       # EvtNext 대기(100ms 는 로그온 직후 디스크 경합에서 배치가 잘려 나감)
ERROR_NO_MORE_ITEMS = 259                         # EvtNext 끝(구현에 따라 빈 목록 대신 이 오류를 낸다)
ERROR_ACCESS_DENIED = 5
ERROR_TIMEOUT = 1460
ERROR_EVT_INVALID_CHANNEL_PATH = 15001
ERROR_EVT_CHANNEL_NOT_FOUND = 15007
EVT_PERMANENT_ERRORS = (ERROR_ACCESS_DENIED, ERROR_EVT_INVALID_CHANNEL_PATH, ERROR_EVT_CHANNEL_NOT_FOUND)
JUMP_FAIL_MAX = 3                                 # 이벤트 로그 일시 오류가 이만큼 연속되면 Jump 감지를 잠시 중단
JUMP_RETRY_SEC = 600.0                            # 중단 뒤 자동 재시도까지
POLL_WINDOW_MAX_SEC = 24 * 3600.0                 # 폴링 공백 보정 창 상한
CAPPED_KEEP_SEC = 24 * 3600.0                     # 캡으로 닫은 conn 을 기억해 두는 시간(늦게 온 closed 보정용)
SEEN_RECORDS_MAX = 500

# 뮤텍스 핸들: 프로세스 종료까지 전역으로 유지(GC 되면 뮤텍스가 풀림)
_MUTEX_HANDLE = None
# 자가 재시작 준비 이벤트 핸들(부모가 spawn 전에 만들고 리셋해 둔다)
_READY_HANDLE = None

log = logging.getLogger("tracker")

# ---------------------------------------------------------------- Win32 준비
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_wtsapi32.WTSQuerySessionInformationW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
_wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
_wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
_wtsapi32.WTSFreeMemory.restype = None


# ---------------------------------------------------------------- Win32 조회 함수
def process_exe_path(pid):
    """pid → 전체 exe 경로. 실패하면 ''."""
    if not pid:
        return ""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return buf.value[:size.value]
    finally:
        _kernel32.CloseHandle(h)


def foreground_window(cfg):
    """(exe 전체경로, 정리된 title, pid). hwnd 0 / 실패 → ('', '', 0)."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return "", "", 0
    try:
        raw_title = win32gui.GetWindowText(hwnd)
    except Exception:
        raw_title = ""
    pid = 0
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = 0
    exe = process_exe_path(pid) if pid else ""
    if not exe:
        # exe 조회 실패는 exe='' , title='' 로 취급 (계약 §6-5)
        return "", "", pid or 0
    title = common.clean_title(raw_title, common.exe_base(exe), cfg)
    return exe, title, pid or 0


def is_locked():
    """WTSINFOEX.SessionFlags 로 잠금 판정. True/False, 모르면 None."""
    buf = ctypes.c_void_p()
    size = wintypes.DWORD(0)
    ok = _wtsapi32.WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTS_SESSION_INFO_EX,
        ctypes.byref(buf), ctypes.byref(size))
    if not ok or not buf.value:
        return None
    try:
        n = int(size.value) // 4
        if n < 5:
            return None
        arr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32 * n)).contents
        flags = int(arr[4])
    finally:
        _wtsapi32.WTSFreeMemory(buf)
    if flags == WTS_SESSIONSTATE_LOCK:
        return True
    if flags == WTS_SESSIONSTATE_UNLOCK:
        return False
    return None


def is_remote():
    """RDP(SM_REMOTESESSION) 여부."""
    try:
        return win32api.GetSystemMetrics(SM_REMOTESESSION) != 0
    except Exception:
        return False


def idle_ms():
    last = win32api.GetLastInputInfo()
    tick = win32api.GetTickCount()
    return (tick - last) & 0xFFFFFFFF


_kernel32.GetTickCount64.restype = ctypes.c_ulonglong
_kernel32.GetTickCount64.argtypes = []


def system_boot_ts():
    """부팅 시각(epoch). GetTickCount64 는 절전 시간을 포함하므로 안정적이다. 실패하면 0.0."""
    try:
        return time.time() - _kernel32.GetTickCount64() / 1000.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- Jump Desktop 이벤트 로그
def evt_fetch_jump_xml(provider, window_ms, max_events=10000):
    """Application 채널에서 provider 의 최근 window_ms 이내 이벤트를 XML 문자열 목록으로 돌려준다(최신순).
    win32evtlog 예외는 호출자가 처리한다(권한/채널 없음 → Jump 감지 끔, 그 외 → 일시 오류로 재시도).
    EvtNext 타임아웃(1460)도 예외로 올린다 — 조용히 '더 없음' 으로 삼으면 배치가 잘려 나가 백필·복원이
    빠진 채로 확정된다."""
    if win32evtlog is None:
        raise RuntimeError("win32evtlog 모듈 없음")
    xpath = ("*[System[Provider[@Name='%s'] and TimeCreated[timediff(@SystemTime) <= %d]]]"
             % (provider.replace("'", "&apos;"), int(window_ms)))
    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    h = win32evtlog.EvtQuery(EVT_CHANNEL, flags, xpath)
    out = []
    try:
        while len(out) < max_events:
            try:
                evs = win32evtlog.EvtNext(h, 50, EVT_NEXT_TIMEOUT_MS)
            except pywintypes.error as e:
                if getattr(e, "winerror", None) == ERROR_NO_MORE_ITEMS:
                    break
                raise
            if not evs:
                break
            for ev in evs:
                out.append(win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml))
    finally:
        h = None  # 핸들은 참조를 놓으면 닫힘
    return out


def is_transient_evt_error(e):
    """이벤트 로그 오류가 일시적인가(서비스 재시작·RPC 불가·타임아웃 등). 권한/채널 없음·모듈 없음은 영구."""
    if pywintypes is None or not isinstance(e, pywintypes.error):
        return False
    return getattr(e, "winerror", None) not in EVT_PERMANENT_ERRORS


def parse_jump_xml_list(xml_list):
    """XML 목록 → parse_jump_event 결과 목록(None 제외)."""
    out = []
    for x in xml_list or []:
        try:
            ev = remote_jump.parse_jump_event(x)
        except Exception:
            ev = None
        if ev is not None:
            out.append(ev)
    return out


class RemoteMonitor(object):
    """RDP + Jump Desktop 이벤트 로그로 원격 여부를 판정한다 (§13.3).

    fetch_xml_fn(window_ms) -> [xml, ...] 와 rdp_fn() -> bool 을 주입할 수 있어 테스트 가능.
    check(now) -> (is_remote, extra) ; extra = {"source": "jump"|"rdp", "conn": id|None}
    """

    def __init__(self, cfg, fetch_xml_fn=None, rdp_fn=None, boot_ts=None):
        rd = dict(common.DEFAULT_CONFIG.get("remote_detect") or {})
        rd.update(cfg.get("remote_detect") or {})
        self.rdp_enabled = bool(rd.get("rdp", True))
        self.jump_enabled = bool(rd.get("jump_eventlog", True))
        self.provider = str(rd.get("jump_provider") or "Jump Desktop Connect")
        self.poll_sec = float(rd.get("poll_sec", 5))
        self.max_session_hours = float(rd.get("max_session_hours", 3))
        self.max_session_sec = self.max_session_hours * 3600.0
        self._fetch = fetch_xml_fn
        self._rdp_fn = rdp_fn or is_remote
        # 부팅 시각: 그 전에 established 된 conn 은 재부팅으로 끊긴 것(closed 가 남지 않는다)
        self.boot_ts = float(system_boot_ts() if boot_ts is None else boot_ts)
        self.open = {}                                 # conn -> established ts
        self.peers = {}                                # conn -> peer (같은 peer 재접속 시 이전 conn 종료 간주)
        self.capped = {}                               # conn -> 캡으로 닫은 시각 (늦게 온 closed 보정용)
        self.late_closed = []                          # [(conn, closed_ts, cap_ts)] — Tracker 가 꺼내 DB 보정
        self.seen = collections.OrderedDict()          # record key -> None (최근 SEEN_RECORDS_MAX 개)
        self.last_poll = None                          # 마지막 폴링 시도 시각(간격 제어)
        self.last_ok_poll = None                       # 마지막으로 성공한 폴링 시각(창 계산 기준)
        self.fail_count = 0                            # 연속 일시 오류 횟수
        self.suspended_until = None                    # 일시 오류 누적으로 감지를 잠시 끈 경우 재시도 시각
        self.rdp = False
        self.last_extra = {"source": "rdp", "conn": None}
        self.stats = {"polls": 0, "events": 0, "fetch_errors": 0}

    # ---------- 이벤트 로그 접근
    def fetch(self, window_ms):
        if self._fetch is not None:
            return self._fetch(window_ms)
        return evt_fetch_jump_xml(self.provider, window_ms)

    def _disable_jump(self, why):
        if self.jump_enabled:
            log.warning("Jump 이벤트 로그 감지 끔(RDP 는 유지): %s", why)
        self.jump_enabled = False
        self.open = {}
        self.peers = {}

    def _fetch_failed(self, e, now, where):
        """fetch 예외 처리. 영구 오류(권한/채널 없음/모듈 없음)는 즉시 끄고, 일시 오류는 JUMP_FAIL_MAX 회까지
        open 을 유지한 채 다음 폴링에서 재시도, 그 이상이면 JUMP_RETRY_SEC 뒤 restore 로 자동 재시도."""
        self.stats["fetch_errors"] += 1
        desc = "%s: %s" % (type(e).__name__, e)
        if not is_transient_evt_error(e):
            self._disable_jump("%s — %s" % (where, desc))
            return
        self.fail_count += 1
        if self.fail_count < JUMP_FAIL_MAX:
            log.warning("Jump 이벤트 로그 %s 실패(%d/%d, 다음 폴링에 재시도, 열린 연결 유지): %s",
                        where, self.fail_count, JUMP_FAIL_MAX, desc)
            return
        self.suspended_until = now + JUMP_RETRY_SEC
        self.open = {}
        self.peers = {}
        log.warning("Jump 이벤트 로그 %s 가 %d회 연속 실패 → %d초 뒤 재시도(그동안 Jump 감지 중단, RDP 는 유지): %s",
                    where, self.fail_count, int(JUMP_RETRY_SEC), desc)

    def _remember(self, ev):
        key = ev.get("record_id") or (ev["ts"], ev["kind"], ev["conn"])
        if key in self.seen:
            return False
        self.seen[key] = None
        while len(self.seen) > SEEN_RECORDS_MAX:
            self.seen.popitem(last=False)
        return True

    # ---------- 시작 시 상태 복원
    def restore(self, now):
        """최근 24h 이벤트로 현재 열린 conn 계산(부팅 이전 established 는 부팅 시각에 닫힌 것으로)."""
        if not self.jump_enabled:
            return {}
        try:
            evs = parse_jump_xml_list(self.fetch(24 * 3600 * 1000))
        except Exception as e:
            self._fetch_failed(e, now, "복원")
            return {}
        self.fail_count = 0
        # 최신순으로 오므로 시간순으로 기억해야 seen 트리밍이 '오래된 것부터' 버린다
        evs.sort(key=lambda e: (e["ts"], e.get("record_id") or 0))
        for ev in evs:
            self._remember(ev)
        self.open = remote_jump.open_connections(evs, self.max_session_hours, now, self.boot_ts)
        self.peers = {}
        for ev in evs:
            if ev["kind"] == "established" and ev["conn"] in self.open:
                self.peers[ev["conn"]] = ev.get("peer")
        self.last_poll = now
        self.last_ok_poll = now
        if self.open:
            log.info("시작 시 Jump 원격 연결 열림: %s", ", ".join(sorted(self.open)))
        return dict(self.open)

    def _poll_window_sec(self, now):
        """폴링 창: 기본 (poll_sec*3+5)초, 마지막 성공 폴링 이후 공백(일시정지·절전·루프 지연)이 더 길면
        그만큼 넓힌다(상한 24h). 공백 동안 로그에 남은 established/closed 를 놓치지 않기 위함."""
        base = self.poll_sec * 3 + 5
        if self.last_ok_poll is None:
            return base
        gap = (now - self.last_ok_poll) + 5
        return min(max(base, gap), POLL_WINDOW_MAX_SEC)

    # ---------- 주기 폴링
    def jump_poll(self, now, force=False):
        if not self.jump_enabled:
            return
        if self.suspended_until is not None:
            if now < self.suspended_until:
                return
            self.suspended_until = None
            self.fail_count = 0
            log.info("Jump 이벤트 로그 감지 재시도(복원)")
            self.restore(now)
            return
        if not force and self.last_poll is not None and now - self.last_poll < self.poll_sec:
            return
        self.last_poll = now
        self.stats["polls"] += 1
        window_sec = self._poll_window_sec(now)
        if window_sec > (self.poll_sec * 3 + 5) * 2:
            log.info("Jump 폴링 공백 %.0f초 → 창 %.0f초로 확대해 읽음", now - (self.last_ok_poll or now), window_sec)
        try:
            evs = parse_jump_xml_list(self.fetch(int(window_sec * 1000)))
        except Exception as e:
            self._fetch_failed(e, now, "폴링")
            return
        self.fail_count = 0
        self.last_ok_poll = now
        # 최신순으로 오므로 시간순으로 처리
        evs.sort(key=lambda e: (e["ts"], e.get("record_id") or 0))
        for ev in evs:
            if not self._remember(ev):
                continue
            self.stats["events"] += 1
            conn = ev["conn"]
            ts = float(ev["ts"])
            if ev["kind"] == "established":
                if ts < self.boot_ts:
                    log.info("Jump 연결 established conn=%s 는 부팅 이전 → 무시", conn)
                    continue
                if conn in self.open or conn in self.capped:
                    continue
                peer = ev.get("peer")
                if peer:
                    # 같은 peer 의 재접속 = 이전 conn 은 (closed 가 누락됐어도) 끝난 것 — build_intervals 와 동일
                    for c2 in [c for c, p in self.peers.items() if p == peer and c != conn]:
                        if c2 in self.open:
                            self.open.pop(c2, None)
                            log.info("같은 peer 재접속(conn=%s) → 이전 conn=%s 종료로 간주", conn, c2)
                        self.peers.pop(c2, None)
                        self.capped.pop(c2, None)
                self.open[conn] = ts
                self.peers[conn] = peer
                log.info("Jump 연결 established conn=%s peer=%s", conn, peer)
            elif ev["kind"] == "closed":
                if conn in self.open:
                    self.open.pop(conn, None)
                    self.peers.pop(conn, None)
                    log.info("Jump 연결 closed conn=%s", conn)
                elif conn in self.capped:
                    cap_ts = self.capped.pop(conn)
                    if ts > cap_ts:
                        self.late_closed.append((conn, ts, cap_ts))
                        log.info("Jump 연결 conn=%s 캡 종료(%.0f초 전) 뒤 closed 도착 → 종료 시각 보정 예정",
                                 conn, now - cap_ts)
        # 닫힘 누락 대비: max_session_hours 넘은 conn 자동 제거(캡). closed 가 나중에 오면 보정한다.
        for conn, st in list(self.open.items()):
            if now - st >= self.max_session_sec:
                self.open.pop(conn, None)
                self.peers.pop(conn, None)
                self.capped[conn] = now
                log.info("Jump 연결 conn=%s 닫힘 이벤트 없이 %.1f시간 경과 → 종료로 간주(캡)",
                         conn, self.max_session_hours)
        for conn, cap_ts in list(self.capped.items()):
            if now - cap_ts > CAPPED_KEEP_SEC:
                self.capped.pop(conn, None)

    def pop_late_closed(self):
        out = list(self.late_closed)
        self.late_closed = []
        return out

    def rdp_now(self):
        if not self.rdp_enabled:
            return False
        try:
            return bool(self._rdp_fn())
        except Exception:
            return False

    def jump_now(self):
        return bool(self.open)

    def _current_extra(self):
        if self.open:
            conn = min(self.open, key=lambda c: self.open[c])   # 먼저 열린 것
            return {"source": "jump", "conn": conn}
        if self.rdp:
            return {"source": "rdp", "conn": None}
        return None

    def check(self, now):
        """(원격 여부, extra). extra 는 켜져 있으면 현재 켜진 소스, 꺼졌으면 마지막으로 켜져 있던 소스."""
        self.rdp = self.rdp_now()
        self.jump_poll(now)
        cur = self._current_extra()
        if cur is not None:
            self.last_extra = cur
            return True, dict(cur)
        return False, dict(self.last_extra)


# ---------------------------------------------------------------- DB 헬퍼
def db_retry(fn, tries=5, wait=0.5, conn=None):
    """sqlite3.OperationalError(locked/busy) 재시도.
    conn 을 주면 재시도 전에 rollback 해서, execute 성공 + commit 실패 뒤 같은 트랜잭션에
    INSERT 가 한 번 더 쌓이는 것(행 중복)을 막는다."""
    last_err = None
    for i in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if "locked" in msg or "busy" in msg:
                log.warning("DB 잠김, 재시도 %d/%d: %s", i + 1, tries, e)
                time.sleep(wait)
                continue
            raise
    raise last_err


def _safe_text(s):
    """창 제목에 짝 없는 서로게이트가 섞이면 sqlite3 가 UnicodeEncodeError 를 낸다 → '?' 로 치환."""
    if not s:
        return ""
    try:
        return s.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        return ""


class Recorder(object):
    """events / sessions 쓰기 담당. 모든 INSERT 는 즉시 commit."""

    def __init__(self, conn):
        self.conn = conn
        self.session_id = None

    def event(self, kind, ts=None, exe="", title="", pid=None, extra=None):
        if ts is None:
            ts = common.now_ts()
        # extra 는 json.dumps 기본 구분자(", " / ": ") 고정 — 백필의 LIKE '%"conn": "<id>"%' 와 맞춰야 한다
        extra_s = json.dumps(extra, ensure_ascii=False) if extra is not None else None

        exe = _safe_text(exe)
        title = _safe_text(title)

        def _do():
            self.conn.execute(
                "INSERT INTO events(ts, kind, exe, title, pid, extra) VALUES(?,?,?,?,?,?)",
                (float(ts), kind, exe, title, pid, extra_s))
            self.conn.commit()
        db_retry(_do, conn=self.conn)
        log.debug("event %s ts=%.3f exe=%s title=%s", kind, ts, common.exe_base(exe), title)

    def close_stale_sessions(self):
        def _do():
            cur = self.conn.execute(
                "UPDATE sessions SET end_ts=last_seen, end_reason='crash' WHERE end_ts IS NULL")
            self.conn.commit()
            return cur.rowcount
        n = db_retry(_do, conn=self.conn)
        if n:
            log.info("이전 미종료 세션 %d개를 crash 로 닫음", n)
        return n

    def open_session(self, ts):
        def _do():
            cur = self.conn.execute(
                "INSERT INTO sessions(start_ts, last_seen, end_ts, end_reason) VALUES(?,?,NULL,NULL)",
                (ts, ts))
            self.conn.commit()
            return cur.lastrowid
        self.session_id = db_retry(_do, conn=self.conn)
        return self.session_id

    def heartbeat(self, ts):
        if self.session_id is None:
            return

        def _do():
            self.conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (ts, self.session_id))
            self.conn.commit()
        db_retry(_do, conn=self.conn)

    def close_session(self, ts, reason="stop"):
        if self.session_id is None:
            return

        def _do():
            self.conn.execute(
                "UPDATE sessions SET last_seen=?, end_ts=?, end_reason=? WHERE id=?",
                (ts, ts, reason, self.session_id))
            self.conn.commit()
        db_retry(_do, conn=self.conn)


# ---------------------------------------------------------------- Jump 백필
def _conn_fragment(conn_id):
    # json.dumps 기본 구분자(", " / ": ") 고정 — Recorder.event 와 맞춘다
    return '"conn": "%s"' % str(conn_id)


def _remote_events_for_conn(conn, kind, conn_id):
    """kind 이벤트 중 extra.conn == conn_id 인 행 [(id, ts, extra dict)] (ts 순).
    instr 선필터(대소문자 구분, 와일드카드 없음) 뒤 파이썬에서 정확히 비교한다 — LIKE 는 대소문자를 무시하고
    '_' 를 한 글자 와일드카드로 봐서 다른 conn 을 '이미 있음' 으로 오판할 수 있다."""
    rows = conn.execute(
        "SELECT id, ts, extra FROM events WHERE kind=? AND instr(extra, ?) > 0 ORDER BY ts, id",
        (kind, _conn_fragment(conn_id))).fetchall()
    out = []
    for r in rows:
        try:
            ex = json.loads(r[2]) if r[2] else {}
        except Exception:
            continue
        if isinstance(ex, dict) and ex.get("conn") == conn_id:
            out.append((r[0], float(r[1]), ex))
    return out


def _remote_event_exists(conn, kind, conn_id):
    return bool(_remote_events_for_conn(conn, kind, conn_id))


def fix_capped_off(conn, conn_id, closed_ts):
    """캡(3h)으로 닫아 둔 remote_off(extra cap:true) 뒤에 실제 closed 가 오면 그 시각으로 종료를 늦춘다.
    캡 시각과 closed 사이에 다른 remote_on 이 있으면(다른 원격이 켜졌음) 건드리지 않는다. 반환 True=보정함."""
    for eid, ts, ex in reversed(_remote_events_for_conn(conn, "remote_off", conn_id)):
        if not ex.get("cap"):
            continue
        if float(closed_ts) <= ts:
            return False
        between = conn.execute(
            "SELECT 1 FROM events WHERE kind='remote_on' AND ts > ? AND ts <= ? LIMIT 1",
            (ts, float(closed_ts))).fetchone()
        if between is not None:
            log.info("conn=%s 캡 종료 보정 생략: 캡(%.0f)~closed(%.0f) 사이에 다른 remote_on 있음",
                     conn_id, ts, float(closed_ts))
            return False
        ex = dict(ex)
        ex["cap"] = False
        ex["cap_ts"] = ts
        ex["late_closed"] = True
        extra_s = json.dumps(ex, ensure_ascii=False)

        def _do():
            conn.execute("UPDATE events SET ts=?, extra=? WHERE id=?", (float(closed_ts), extra_s, eid))
            conn.commit()
        db_retry(_do, conn=conn)
        log.info("conn=%s 캡 종료(%s) → 실제 closed(%s) 로 remote_off 보정", conn_id,
                 datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                 datetime.datetime.fromtimestamp(float(closed_ts)).strftime("%Y-%m-%d %H:%M:%S"))
        return True
    return False


def _load_session_ranges(conn, cfg):
    """[(start, end)] — 트래커가 살아 있던 시간(엔진 커버리지와 같은 규칙: (end_ts or last_seen)+heartbeat)."""
    hb = float(cfg.get("heartbeat_sec", 30))
    out = []
    try:
        for r in conn.execute("SELECT start_ts, last_seen, end_ts FROM sessions").fetchall():
            st = float(r[0])
            en = float(r[2]) if r[2] is not None else float(r[1])
            out.append((st, en + hb))
    except Exception:
        log.exception("sessions 읽기 실패")
    return out


def _session_covers(ranges, ts):
    return any(a <= ts <= b for a, b in ranges)


def _live_remote_state_on(conn, ts):
    """ts 시점에 '라이브(백필 아님)' remote_on/off 만 놓고 본 원격 상태가 켜짐인가."""
    row = conn.execute(
        "SELECT kind FROM events WHERE kind IN ('remote_on','remote_off') AND ts <= ? "
        "AND (extra IS NULL OR instr(extra, '\"backfill\": true') = 0) ORDER BY ts DESC, id DESC LIMIT 1",
        (float(ts),)).fetchone()
    return row is not None and row[0] == "remote_on"


def _other_interval_covers(intervals, conn_id, ts):
    for st, end, c in intervals:
        if c == conn_id:
            continue
        if st <= ts and (end is None or end > ts):
            return True
    return False


def backfill_jump(conn, cfg, now, fetch_xml_fn=None, boot_ts=None):
    """최근 backfill_days 일의 Jump 이벤트로 remote_on/off 를 없을 때만 INSERT (§13.3).

    remote_on/off 는 '전체 원격 상태' 전이라서 conn 단위로 무조건 넣으면 진행 중인 원격 구간 안에 off 가 끼어든다.
    그래서 remote_off 는 (a) 다른 Jump 구간이 그 시각을 덮고 있거나 (b) 라이브 이벤트 기준 그 시각의 상태가
    켜짐이고 트래커 세션이 그 시각을 덮고 있으면(= 살아 있던 수집기가 다른 소스 때문에 끄지 않은 것) 넣지 않는다.
    캡(3h)으로 닫힌 remote_off 뒤에 실제 closed 가 있으면 종료 시각을 보정한다.
    반환 {"intervals", "inserted", "skipped", "skipped_overlap", "fixed", "disabled"}."""
    rd = dict(common.DEFAULT_CONFIG.get("remote_detect") or {})
    rd.update(cfg.get("remote_detect") or {})
    result = {"intervals": 0, "inserted": 0, "skipped": 0, "skipped_overlap": 0, "fixed": 0, "disabled": False}
    if not rd.get("jump_eventlog", True):
        result["disabled"] = True
        return result
    days = float(rd.get("backfill_days", 7))
    provider = str(rd.get("jump_provider") or "Jump Desktop Connect")
    window_ms = int(days * 86400 * 1000)
    try:
        if fetch_xml_fn is not None:
            xmls = fetch_xml_fn(window_ms)
        else:
            xmls = evt_fetch_jump_xml(provider, window_ms)
    except Exception as e:
        log.warning("Jump 백필: 이벤트 로그 읽기 실패 — %s: %s", type(e).__name__, e)
        result["disabled"] = True
        return result
    if boot_ts is None:
        boot_ts = system_boot_ts()
    evs = parse_jump_xml_list(xmls)
    intervals = remote_jump.build_intervals(evs, rd.get("max_session_hours", 3), now, boot_ts)
    result["intervals"] = len(intervals)
    ranges = _load_session_ranges(conn, cfg)
    rec = Recorder(conn)
    for st, end, conn_id in intervals:
        if _remote_event_exists(conn, "remote_on", conn_id):
            result["skipped"] += 1
        else:
            rec.event("remote_on", ts=st, extra={"source": "jump", "conn": conn_id, "backfill": True})
            result["inserted"] += 1
            log.info("Jump 백필 remote_on ts=%s conn=%s",
                     datetime.datetime.fromtimestamp(st).strftime("%Y-%m-%d %H:%M:%S"), conn_id)
        if end is None:
            continue
        if _remote_event_exists(conn, "remote_off", conn_id):
            result["skipped"] += 1
            if fix_capped_off(conn, conn_id, end):
                result["fixed"] += 1
            continue
        if _other_interval_covers(intervals, conn_id, end):
            result["skipped_overlap"] += 1
            log.info("Jump 백필 remote_off 생략 conn=%s ts=%s: 다른 Jump 연결이 진행 중", conn_id,
                     datetime.datetime.fromtimestamp(end).strftime("%Y-%m-%d %H:%M:%S"))
            continue
        if _session_covers(ranges, end) and _live_remote_state_on(conn, end):
            result["skipped_overlap"] += 1
            log.info("Jump 백필 remote_off 생략 conn=%s ts=%s: 수집기가 살아 있었고 그 시각 원격 상태가 켜짐(다른 소스)",
                     conn_id, datetime.datetime.fromtimestamp(end).strftime("%Y-%m-%d %H:%M:%S"))
            continue
        rec.event("remote_off", ts=end, extra={"source": "jump", "conn": conn_id, "backfill": True})
        result["inserted"] += 1
        log.info("Jump 백필 remote_off ts=%s conn=%s",
                 datetime.datetime.fromtimestamp(end).strftime("%Y-%m-%d %H:%M:%S"), conn_id)
    log.info("Jump 백필 완료: 이벤트 %d건 → 구간 %d개, 삽입 %d, 이미 있음 %d, 겹침 생략 %d, 캡 보정 %d",
             len(evs), result["intervals"], result["inserted"], result["skipped"],
             result["skipped_overlap"], result["fixed"])
    return result


# ---------------------------------------------------------------- 백업
BACKUP_RETRY_SEC = 600.0
_backup_retry_after = 0.0   # 백업 실패 후 이 monotonic 시각까지는 재시도하지 않음(30초마다 DB 통째 복사 반복 방지)


def maybe_backup(conn, cfg, ddir):
    """state.last_backup_day != 오늘이면 backup/worklog-YYYY-MM-DD.db 저장 + 오래된 것 삭제."""
    global _backup_retry_after
    today = common.today_str(cfg)
    last = common.get_state(conn, "last_backup_day")
    if last == today:
        return False
    if time.monotonic() < _backup_retry_after:
        return False
    # 테스트 DB(MAENG_WORKLOG_DB / --db)로 실행 중이면 그 DB 옆의 backup\ 에 저장해
    # 실제 data_dir/backup 의 오늘 백업을 테스트 데이터로 덮어쓰지 않는다.
    if os.environ.get("MAENG_WORKLOG_DB"):
        bdir = os.path.join(os.path.dirname(os.path.abspath(common.db_path(cfg))), "backup")
    else:
        bdir = os.path.join(ddir, "backup")
    os.makedirs(bdir, exist_ok=True)
    dest = os.path.join(bdir, "worklog-%s.db" % today)
    tmp = dest + ".tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        bconn = sqlite3.connect(tmp)
        try:
            conn.backup(bconn)
        finally:
            bconn.close()
        os.replace(tmp, dest)
    except Exception as e:
        # 대상 파일이 다른 프로그램(DB Browser 등)에 열려 있으면 os.replace 가 WinError 로 실패한다.
        # 10분 뒤에 다시 시도하고, 그동안은 경고 1줄만 남긴다.
        _backup_retry_after = time.monotonic() + BACKUP_RETRY_SEC
        log.warning("백업 실패(%d초 뒤 재시도): %s — %s: %s", int(BACKUP_RETRY_SEC), dest,
                    type(e).__name__, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False
    _backup_retry_after = 0.0
    common.set_state(conn, "last_backup_day", today)
    log.info("백업 저장: %s", dest)

    # 오래된 백업 정리
    keep_days = int(cfg.get("backup_keep_days", 14))
    cutoff = datetime.date.today() - datetime.timedelta(days=keep_days)
    try:
        for name in os.listdir(bdir):
            if not (name.startswith("worklog-") and name.endswith(".db")):
                continue
            try:
                d = datetime.datetime.strptime(name[len("worklog-"):-3], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d < cutoff:
                try:
                    os.remove(os.path.join(bdir, name))
                    log.info("오래된 백업 삭제: %s", name)
                except Exception:
                    log.exception("백업 삭제 실패: %s", name)
    except Exception:
        log.exception("백업 폴더 정리 실패")
    return True


# ---------------------------------------------------------------- 자가 재시작 / 서버 감시 헬퍼
def pythonw_path():
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def code_mtimes():
    """{파일명: mtime}. 읽기 실패한 파일은 뺀다."""
    out = {}
    for name in CODE_FILES:
        try:
            out[name] = os.path.getmtime(os.path.join(common.ROOT, name))
        except Exception:
            pass
    return out


def code_compiles():
    """CODE_FILES 가 모두 컴파일되면 True. 실패하면 로그 남기고 False."""
    tmpdir = tempfile.mkdtemp(prefix="maeng_pyc_")
    try:
        for name in CODE_FILES:
            src = os.path.join(common.ROOT, name)
            if not os.path.exists(src):
                continue
            cfile = os.path.join(tmpdir, name + "c")
            try:
                py_compile.compile(src, cfile=cfile, doraise=True)
            except Exception as e:
                log.warning("코드 변경 감지했으나 %s 컴파일 실패 → 재시작 보류: %s", name, e)
                return False
        return True
    finally:
        try:
            for n in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, n))
            os.rmdir(tmpdir)
        except Exception:
            pass


def spawn_detached(cmd, env=None):
    return subprocess.Popen(
        cmd, cwd=common.ROOT, creationflags=DETACHED, close_fds=True, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def spawn_self(argv_rest):
    """자기 자신을 pythonw 로 새로 띄운다(같은 인자). Popen 반환."""
    env = dict(os.environ)
    env[RESTART_ENV] = "1"
    cmd = [pythonw_path(), os.path.join(common.ROOT, "tracker.py")] + list(argv_rest)
    return spawn_detached(cmd, env=env)


def server_env():
    """서버 감시로 띄우는 server.py 환경: 테스트 DB 지정(MAENG_WORKLOG_DB)은 물려주지 않는다
    (--db 로 띄운 트래커가 실제 포트에 테스트 DB 서버를 올리는 것 방지)."""
    env = dict(os.environ)
    env.pop("MAENG_WORKLOG_DB", None)
    env.pop(RESTART_ENV, None)
    return env


def spawn_server():
    cmd = [pythonw_path(), os.path.join(common.ROOT, "server.py")]
    return spawn_detached(cmd, env=server_env())


# ---------------------------------------------------------------- 자가 재시작 핸드오프(뮤텍스·준비 이벤트)
def release_mutex():
    global _MUTEX_HANDLE
    h = _MUTEX_HANDLE
    _MUTEX_HANDLE = None
    if h is not None:
        try:
            h.Close()
        except Exception:
            pass


def reset_ready_event():
    """부모: 준비 이벤트를 만들고(이미 있으면 그 핸들) 신호를 지운다. spawn 전에 호출."""
    global _READY_HANDLE
    _READY_HANDLE = win32event.CreateEvent(None, True, False, READY_EVENT_NAME)
    win32event.ResetEvent(_READY_HANDLE)


def wait_ready_event(timeout_sec):
    """부모: 준비 이벤트가 켜졌으면 True."""
    if _READY_HANDLE is None:
        return False
    rc = win32event.WaitForSingleObject(_READY_HANDLE, int(max(0.0, float(timeout_sec)) * 1000))
    return rc == win32event.WAIT_OBJECT_0


def signal_ready():
    """자식: start() 를 마친 뒤 부모에게 준비 완료를 알린다."""
    try:
        h = win32event.CreateEvent(None, True, False, READY_EVENT_NAME)
        win32event.SetEvent(h)
        h.Close()
    except Exception:
        log.exception("준비 신호 실패")


def handoff_to_child(proc, wait_sec=None, release_fn=None, wait_ready_fn=None, acquire_fn=None,
                     kill_fn=None, poll_sec=0.5):
    """부모가 stop()·DB close 를 마친 뒤 호출. 뮤텍스를 놓고 자식이 start() 를 마쳤다는 준비 신호를 기다린다.
    True = 자식이 준비됨(현재 프로세스는 종료). False = 자식 실패(뮤텍스를 다시 잡았으니 계속 실행할 것).
    뮤텍스를 다시 잡지 못하면(다른 수집기가 먼저 잡음) True."""
    wait_sec = RESTART_READY_WAIT_SEC if wait_sec is None else float(wait_sec)
    release_fn = release_fn or release_mutex
    wait_ready_fn = wait_ready_fn or wait_ready_event
    acquire_fn = acquire_fn or acquire_mutex

    def _kill(p):
        try:
            p.kill()
        except Exception:
            pass
    kill_fn = kill_fn or _kill

    release_fn()
    end = time.monotonic() + wait_sec
    ready = False
    while True:
        if wait_ready_fn(poll_sec):
            ready = True
            break
        rc = proc.poll()
        if rc is not None:
            log.warning("새 프로세스가 준비 전에 종료됨(rc=%s)", rc)
            break
        if time.monotonic() >= end:
            log.warning("새 프로세스가 %.0f초 안에 준비되지 않음 → 강제 종료", wait_sec)
            kill_fn(proc)
            break
    if ready:
        log.info("새 프로세스 pid=%s 준비 완료 → 현재 프로세스 종료", getattr(proc, "pid", None))
        return True
    if not acquire_fn(retry_sec=10.0):
        log.warning("뮤텍스를 다시 잡지 못함(다른 수집기가 실행 중) → 현재 프로세스 종료")
        return True
    log.warning("자가 재시작 실패 → 현재 프로세스가 새 세션으로 계속 실행")
    return False


def port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
    except Exception:
        return False
    try:
        s.close()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- 트래커 본체
class Tracker(object):
    def __init__(self, cfg, conn, ddir, seconds=None, monitor=None, argv_rest=None,
                 enable_self_update=None, enable_server_watch=None):
        self.cfg = cfg
        self.conn = conn
        self.ddir = ddir
        self.seconds = seconds
        self.argv_rest = list(argv_rest or [])
        self.rec = Recorder(conn)
        self.poll_sec = float(cfg.get("poll_sec", 2))
        self.heartbeat_sec = float(cfg.get("heartbeat_sec", 30))
        self.idle_sec = float(cfg.get("idle_sec", 600))
        self.running = True
        self.restart_requested = False
        self.restart_proc = None    # 자가 재시작으로 띄운 자식(Popen) — main 이 핸드오프에 사용
        # 상태
        self.paused = False
        self.remote = False
        self.locked = False
        self.idle = False
        self.last_app = None        # (exe, title) 마지막으로 기록한 전경 창; None 이면 다음 루프에 반드시 기록
        self.idle_floor_ts = 0.0    # idle_on 소급 하한: 세션 start · 직전 idle_off 이전으로는 소급하지 않음
        self.last_heartbeat = 0.0
        self.stats = {"app": 0, "loops": 0, "errors": 0}
        # 원격 감시
        self.monitor = monitor if monitor is not None else RemoteMonitor(cfg)
        # 자가 재시작 (§13.4) — --seconds 디버그 실행에서는 끔
        debug = seconds is not None
        self.self_update_sec = float(cfg.get("self_update_check_sec", 30) or 0)
        self.self_update_enabled = (not debug and self.self_update_sec > 0) \
            if enable_self_update is None else bool(enable_self_update)
        self.code_mtimes0 = code_mtimes()
        self.last_self_update_check = time.monotonic()
        # 서버 감시 (§13.4) — 테스트 DB(--db / MAENG_WORKLOG_DB)로 도는 트래커는 실제 포트에 서버를 띄우지 않는다
        test_db = bool(os.environ.get("MAENG_WORKLOG_DB"))
        self.server_watch_enabled = (not debug and not test_db) \
            if enable_server_watch is None else bool(enable_server_watch)
        self.server_port = int(cfg.get("port", 8771))
        self.server_down_since = None
        self.server_retry_after = 0.0

    # ---------------- 상태 전이 헬퍼
    def _reset_app_state(self):
        """resume/unlock 직후 현재 전경 창이 app 이벤트로 다시 기록되게 한다."""
        self.last_app = None

    def read_paused_until(self):
        val = common.get_state(self.conn, "paused_until")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # ---------------- 시작 / 종료
    def start(self):
        now = common.now_ts()
        self.rec.close_stale_sessions()
        sid = self.rec.open_session(now)
        self.rec.event("start", ts=now)
        self.idle_floor_ts = now
        self.last_heartbeat = now
        log.info("세션 시작 id=%s db=%s", sid, common.db_path(self.cfg))

        # 초기 상태 1회 조회 (플래그는 이벤트 INSERT 성공 뒤에 바꾼다)
        lk = self._safe(is_locked)
        if lk is True:
            self.rec.event("lock", ts=now)
            self.locked = True
            log.info("시작 시 잠금 상태")
        # Jump 백필(먼저) → 현재 원격 상태 복원
        self._safe(backfill_jump, self.conn, self.cfg, now, self.monitor._fetch, self.monitor.boot_ts)
        self._safe(self.monitor.restore, now)
        rm, extra = self.monitor.check(now)
        if rm:
            self.rec.event("remote_on", ts=now, extra=extra)
            self.remote = True
            log.info("시작 시 원격 세션 %s", extra)
        # 백업 확인
        self._safe(maybe_backup, self.conn, self.cfg, self.ddir)

    def stop(self, reason="stop"):
        now = common.now_ts()
        try:
            self.rec.event("stop", ts=now)
        except Exception:
            log.exception("stop 이벤트 기록 실패")
        try:
            self.rec.close_session(now, reason)
        except Exception:
            log.exception("세션 마감 실패")
        log.info("세션 종료 reason=%s app_events=%d loops=%d errors=%d",
                 reason, self.stats["app"], self.stats["loops"], self.stats["errors"])

    def _safe(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            log.exception("%s 실패", getattr(fn, "__name__", "fn"))
            self.stats["errors"] += 1
            return None

    # ---------------- 루프 한 번
    def step(self):
        now = common.now_ts()
        self.stats["loops"] += 1

        # 1~5 는 예외가 나도 6·7(heartbeat/백업)이 굶지 않도록 따로 감싼다.
        # (DB 잠김·GetLastInputInfo 실패가 매 루프 반복되면 heartbeat 가 끊겨 서버가 '끊김'으로 보게 됨)
        try:
            self._step_observe(now)
        except Exception:
            self.stats["errors"] += 1
            log.exception("관찰 단계 예외 (heartbeat 는 계속)")

        # 6. heartbeat (paused 중에도 유지)
        if now - self.last_heartbeat >= self.heartbeat_sec:
            self.rec.heartbeat(now)
            self.last_heartbeat = now
            # 7. 날짜가 바뀌었으면 백업
            self._safe(maybe_backup, self.conn, self.cfg, self.ddir)
            # 8. 서버 감시
            if self.server_watch_enabled:
                self._safe(self._watch_server, now)

        # 9. 코드 변경 → 자가 재시작
        if self.self_update_enabled and self.running:
            if time.monotonic() - self.last_self_update_check >= self.self_update_sec:
                self.last_self_update_check = time.monotonic()
                self._safe(self._check_self_update)

    def _apply_late_closed(self):
        """캡으로 닫은 conn 의 closed 가 늦게 도착 → 그 remote_off 의 시각을 실제 closed 로 보정."""
        for conn_id, closed_ts, _cap_ts in self.monitor.pop_late_closed():
            self._safe(fix_capped_off, self.conn, conn_id, closed_ts)

    def _step_remote(self, now):
        """2. 원격 (RDP + Jump). 전이 시 remote_on/off, extra={"source","conn"}.
        캡(3h)으로 꺼진 경우 extra 에 cap:true 를 남겨 나중에 closed 가 오면 보정할 수 있게 한다."""
        rm, extra = self.monitor.check(now)
        self._apply_late_closed()
        if rm and not self.remote:
            self.rec.event("remote_on", ts=now, extra=extra)
            self.remote = True
            log.info("원격 세션 시작 %s", extra)
        elif not rm and self.remote:
            if extra.get("conn") in self.monitor.capped:
                extra["cap"] = True
            self.rec.event("remote_off", ts=now, extra=extra)
            self.remote = False
            log.info("원격 세션 종료 %s", extra)

    def _step_observe(self, now):
        """루프 1~5 단계. 상태 플래그는 반드시 rec.event() 가 정상 반환한 **뒤에** 바꾼다
        (INSERT 실패 시 다음 루프에서 같은 전이를 다시 시도하게)."""
        # 1. 일시정지
        until = self.read_paused_until()
        paused_now = until is not None and now < until
        if paused_now and not self.paused:
            self.rec.event("pause", ts=now, extra={"until": until})
            self.paused = True
            log.info("일시정지 진입 until=%s", until)
        elif not paused_now and self.paused:
            self.rec.event("resume", ts=now)
            self.paused = False
            # 잠금/원격/idle 상태는 유지하되, 전경 창은 다시 기록되게 리셋
            self._reset_app_state()
            log.info("일시정지 해제")

        if self.paused:
            # 일시정지 중에도 Jump 로그는 계속 읽어 열린 연결 상태를 최신으로 유지한다(이벤트 기록은 해제 뒤 전이에서).
            # 안 읽으면 그 사이의 established/closed 가 폴링 창 밖으로 나가 영영 놓친다.
            self.monitor.jump_poll(now)
            self._apply_late_closed()
            return

        # 2. 원격
        self._step_remote(now)

        # 3. 잠금
        lk = is_locked()
        if lk is True and not self.locked:
            self.rec.event("lock", ts=now)
            self.locked = True
            log.info("잠금")
        elif lk is False and self.locked:
            self.rec.event("unlock", ts=now)
            self.locked = False
            self._reset_app_state()
            log.info("잠금 해제")
        # lk None → 상태 유지

        # 4. idle
        ims = idle_ms()
        if not self.idle and ims >= self.idle_sec * 1000:
            # 소급 ts 하한: 세션 start · 직전 idle_off 이전으로는 소급하지 않는다.
            # (그보다 앞서면 엔진의 (ts,id) 정렬에서 start/idle_off 뒤로 밀려 idle 이 통째로 지워진다)
            ts = max(now - ims / 1000.0, self.idle_floor_ts)
            self.rec.event("idle_on", ts=ts)
            self.idle = True
            log.info("idle_on (소급 %.0f초)", now - ts)
        elif self.idle and ims < self.idle_sec * 1000:
            self.rec.event("idle_off", ts=now)
            self.idle = False
            self.idle_floor_ts = now
            log.info("idle_off")

        # 5. 전경 창 (잠금 중엔 기록 안 함)
        if not self.locked:
            exe, title, pid = foreground_window(self.cfg)
            key = (exe, title)
            if key != self.last_app:
                self.rec.event("app", ts=now, exe=exe, title=title, pid=pid or None)
                self.last_app = key
                self.stats["app"] += 1

    # ---------------- 서버 감시 (§13.4)
    def _watch_server(self, now, port_open_fn=None, spawn_fn=None):
        mono = time.monotonic()
        ok = (port_open_fn or port_open)(self.server_port)
        if ok:
            self.server_down_since = None
            return False
        if self.server_down_since is None:
            self.server_down_since = mono
            return False
        if mono - self.server_down_since < SERVER_DOWN_SEC:
            return False
        if mono < self.server_retry_after:
            return False
        self.server_retry_after = mono + SERVER_SPAWN_COOLDOWN_SEC
        log.info("서버 포트 %d 가 %.0f초 이상 닫혀 있음 → pythonw server.py 실행",
                 self.server_port, mono - self.server_down_since)
        (spawn_fn or spawn_server)()
        return True

    # ---------------- 자가 재시작 (§13.4)
    def _code_changed(self):
        cur = code_mtimes()
        for name, m0 in self.code_mtimes0.items():
            m1 = cur.get(name)
            if m1 is not None and m1 > m0 + 1e-6:
                return name
        return None

    def _check_self_update(self, spawn_fn=None, compiles_fn=None, grace_sec=None, reset_ready_fn=None):
        """코드가 바뀌었으면 자식을 띄우고 루프를 끝낸다. 자식은 뮤텍스를 기다리느라 부모가 끝날 때까지
        start() 에 못 가므로 여기서의 짧은 생존 확인은 import 오류만 잡는다 — 진짜 확인은 main 의
        handoff_to_child(준비 신호 대기, 실패 시 뮤텍스를 되찾아 계속 실행)."""
        changed = self._code_changed()
        if not changed:
            return False
        log.info("코드 변경 감지(%s) → 재시작", changed)
        if not (compiles_fn or code_compiles)():
            return False   # 다음 검사 때 재시도
        try:
            (reset_ready_fn or reset_ready_event)()
        except Exception:
            log.exception("준비 이벤트 리셋 실패(계속)")
        proc = (spawn_fn or spawn_self)(self.argv_rest)
        grace = RESTART_CHILD_GRACE_SEC if grace_sec is None else float(grace_sec)
        end = time.monotonic() + grace
        while time.monotonic() < end:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if proc.poll() is not None:
            log.warning("새 프로세스가 곧바로 종료됨(rc=%s) → 현재 프로세스 계속 실행", proc.returncode)
            # 다음 검사에서 또 시도하지 않도록 현재 mtime 을 기준으로 삼는다
            self.code_mtimes0 = code_mtimes()
            return False
        log.info("새 프로세스 pid=%s 기동 → 세션 마감 후 준비 신호를 기다린다", proc.pid)
        self.restart_proc = proc
        self.restart_requested = True
        self.running = False
        return True

    # ---------------- 메인 루프
    def run(self):
        deadline = None
        if self.seconds is not None:
            deadline = time.monotonic() + float(self.seconds)
        while self.running:
            try:
                self.step()
            except Exception:
                self.stats["errors"] += 1
                log.exception("루프 예외 (계속)")
            # 대기 (종료 요청은 즉시 반영)
            if deadline is not None and time.monotonic() >= deadline:
                log.info("--seconds 만료, 정상 종료")
                break
            wait_end = time.monotonic() + self.poll_sec
            while self.running and time.monotonic() < wait_end:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.2)
            if deadline is not None and time.monotonic() >= deadline:
                log.info("--seconds 만료, 정상 종료")
                break


# ---------------------------------------------------------------- 진입점
def setup_logging(ddir):
    path = os.path.join(ddir, "tracker.log")
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1000000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 콘솔 핸들러 금지 — 파일만
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    return path


def acquire_mutex(retry_sec=0.0, interval=MUTEX_RETRY_INTERVAL):
    """뮤텍스 획득. retry_sec > 0 이면 그 시간 동안 interval 간격으로 재시도(자가 재시작 자식용)."""
    global _MUTEX_HANDLE
    end = time.monotonic() + float(retry_sec or 0)
    while True:
        _MUTEX_HANDLE = win32event.CreateMutex(None, False, MUTEX_NAME)
        err = win32api.GetLastError()
        if err != ERROR_ALREADY_EXISTS:
            return True
        if time.monotonic() >= end:
            return False
        try:
            _MUTEX_HANDLE.Close()
        except Exception:
            pass
        _MUTEX_HANDLE = None
        time.sleep(interval)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="MAENG_worklog tracker")
    p.add_argument("--seconds", type=float, default=None, help="N초 후 정상 종료(디버그)")
    p.add_argument("--db", default=None, help="DB 경로 재지정(환경변수보다 우선)")
    p.add_argument("--backfill-only", action="store_true",
                   help="Jump Desktop 이벤트 로그 백필만 수행하고 종료(뮤텍스·세션 없음)")
    return p.parse_args(argv)


def run_backfill_only(cfg):
    conn = common.connect(cfg, common.db_path(cfg))
    try:
        res = backfill_jump(conn, cfg, common.now_ts())
    finally:
        conn.close()
    return res


def main(argv=None):
    # pythonw 에서는 sys.stdout/stderr 가 None 이라 argparse 오류 출력·--seconds 요약 출력이 AttributeError 를 낸다.
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    if args.db:
        os.environ["MAENG_WORKLOG_DB"] = args.db

    cfg = common.load_config()
    ddir = common.data_dir(cfg)
    setup_logging(ddir)

    if args.backfill_only:
        res = run_backfill_only(cfg)
        try:
            sys.stdout.write("backfill: db=%s intervals=%d inserted=%d skipped=%d disabled=%s\n" % (
                common.db_path(cfg), res["intervals"], res["inserted"], res["skipped"], res["disabled"]))
        except Exception:
            pass
        return

    retry = MUTEX_RETRY_SEC if os.environ.get(RESTART_ENV) else 0.0
    if not acquire_mutex(retry_sec=retry):
        log.info("이미 실행 중(뮤텍스 존재) — 종료")
        if args.seconds is not None:
            sys.stderr.write("tracker: already running, exit\n")
        sys.exit(0)
    ready_fn = None
    if os.environ.get(RESTART_ENV):
        log.info("자가 재시작으로 기동(pid=%d)", os.getpid())
        os.environ.pop(RESTART_ENV, None)
        ready_fn = signal_ready

    # 세션 루프: 보통은 1회. 자가 재시작 자식이 준비 신호를 못 보내고 죽으면 뮤텍스를 되찾아 새 세션으로 계속.
    tracker = None
    while True:
        tracker = run_tracker_session(cfg, ddir, args, argv, ready_fn=ready_fn)
        ready_fn = None
        if tracker is None or not tracker.restart_requested or tracker.restart_proc is None:
            break
        if handoff_to_child(tracker.restart_proc):
            break
        # 실패한 변경으로 또 재시작하지 않도록: 새 Tracker 가 생성 시점의 mtime 을 기준으로 잡는다
    if args.seconds is not None and tracker is not None and sys.stderr:
        sys.stderr.write("tracker: done (%d loops, %d app events, %d errors)\n" % (
            tracker.stats["loops"], tracker.stats["app"], tracker.stats["errors"]))


def run_tracker_session(cfg, ddir, args, argv, ready_fn=None):
    """DB 연결 → Tracker → start → (준비 신호) → run → stop → close. Tracker 를 돌려준다(없으면 None)."""
    conn = None
    tracker = None
    exit_reason = "stop"
    try:
        conn = common.connect(cfg, common.db_path(cfg))
        tracker = Tracker(cfg, conn, ddir, seconds=args.seconds, argv_rest=list(argv))

        def _on_signal(signum, frame):
            log.info("시그널 %s 수신 — 종료 요청", signum)
            tracker.running = False

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _on_signal)
            except Exception:
                pass
        try:
            signal.signal(signal.SIGBREAK, _on_signal)
        except Exception:
            pass

        tracker.start()
        if ready_fn is not None:
            ready_fn()
        tracker.run()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — 종료")
    except Exception:
        log.exception("치명적 오류로 종료")
        exit_reason = "stop"
    finally:
        if tracker is not None:
            try:
                tracker.stop(exit_reason)
            except Exception:
                log.exception("종료 처리 실패")
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return tracker


if __name__ == "__main__":
    main()
