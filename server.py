# -*- coding: utf-8 -*-
"""
server.py — MAENG_worklog UI 서버 + JSON API (Python 3.8, 표준 라이브러리만)

- http.server.ThreadingHTTPServer 기반. 정적 파일(static/) + /api/* JSON.
- DESIGN.md §8 의 API 표를 그대로 구현한다. §11.2 의 /api/range, /api/restart 포함.
- engine.py 가 아직 없거나 import 에 실패해도 서버는 뜬다(/api/day 만 500).
- 실행: python server.py [--port N] [--db PATH] [--host H] [--debug]
  (pythonw 로 숨김 실행되므로 콘솔 출력은 하지 않고 로그 파일만 쓴다: data_dir/server.log)
"""
import os
import re
import sys
import json
import math
import time
import socket
import logging
import argparse
import datetime
import hashlib
import hmac
import secrets
import shutil
import base64
import binascii
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from html import escape as html_escape

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402

try:
    import engine  # noqa: E402
    ENGINE_ERROR = None
except Exception as _e:  # 다른 에이전트가 만드는 중이거나 문법 오류 → 서버는 계속 뜬다
    engine = None
    ENGINE_ERROR = "%s: %s" % (type(_e).__name__, _e)

try:
    import anki_stats  # noqa: E402  (Anki 복습 모니터, DESIGN §31)
    ANKI_ERROR = None
except Exception as _e:
    anki_stats = None
    ANKI_ERROR = "%s: %s" % (type(_e).__name__, _e)

try:
    import plans as my_events  # noqa: E402  (내 일정·마감, DESIGN §28; 표 이름 plans)
    EVENTS_ERROR = None
except Exception as _e:
    my_events = None
    EVENTS_ERROR = "%s: %s" % (type(_e).__name__, _e)

try:
    import schedule as academic_schedule  # noqa: E402  (대학원 학사일정, DESIGN §24)
    SCHEDULE_ERROR = None
except Exception as _e:
    academic_schedule = None
    SCHEDULE_ERROR = "%s: %s" % (type(_e).__name__, _e)

try:
    import notices  # noqa: E402  (학교 공지, DESIGN §16)
    NOTICES_ERROR = None
except Exception as _e:  # 공지 모듈이 깨져도 출근기록 본체는 멀쩡해야 한다
    notices = None
    NOTICES_ERROR = "%s: %s" % (type(_e).__name__, _e)

log = logging.getLogger("MAENG_worklog.server")
CFG = {}  # main() 에서 채움

_rules_lock = threading.Lock()
_notes_lock = threading.Lock()
_notices_cfg_lock = threading.Lock()    # notices.json 읽기 (§15 의 규칙 파일과 같은 방식)
_notices_busy = threading.Lock()        # 수집 1개만 — 진행 중이면 재요청 무시
# §17 본문 분석: claude CLI 호출은 느리고 토큰을 쓴다. 동시 2개까지, 같은 글은 1개만.
NOTICE_ANALYZE_MAX = 2
_notices_analyze_slots = threading.BoundedSemaphore(NOTICE_ANALYZE_MAX)
_notices_analyzing = set()
_notices_analyzing_lock = threading.Lock()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_YEAR_MIN, DATE_YEAR_MAX = 1990, 2100   # Windows mktime 은 1970 이전을 못 다룸 → 500 대신 400
PINNED_NOTE_FILE = "고정메모.md"
# UI(팝오버)에서 추가하는 규칙의 priority. 제목 규칙은 기본 제목 규칙(최대 300)보다 높아야
# "이 제목은 앞으로 항상 → X" 가 실제로 먹는다. exe 규칙은 '제목 규칙 > exe 규칙' 원칙을 지켜 150 유지.
UI_RULE_PRIORITY = {"exe": 150, "title": 350}
# 이 kind 그룹별로 window 이전 마지막 이벤트 1건씩을 앞에 붙여 window.start 시점 상태를 복원한다
_STATE_KIND_GROUPS = (
    ("start", "stop"), ("app",), ("lock", "unlock"),
    ("idle_on", "idle_off"), ("remote_on", "remote_off"), ("pause", "resume"),
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}

FALLBACK_INDEX = u"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>Orbit · 출근기록</title></head>
<body style="font-family:'Malgun Gothic','Segoe UI',sans-serif;padding:24px;color:#333">
<h2>Orbit · 출근기록</h2>
<p>서버는 실행 중이지만 <code>static/index.html</code> 이 아직 없습니다.</p>
<p>API 확인: <a href="/api/status">/api/status</a> · <a href="/api/day">/api/day</a> ·
<a href="/api/todos">/api/todos</a> · <a href="/api/config">/api/config</a></p>
</body></html>"""


# ------------------------------------------------------------------ 공용 유틸
class ApiError(Exception):
    """HTTP 상태코드 + 메시지를 담는 예외. 핸들러가 {"error": msg} 로 변환."""

    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg


def _valid_date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        return False
    try:
        d = datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return False
    return DATE_YEAR_MIN <= d.year <= DATE_YEAR_MAX


def _require_date(s, what="date"):
    if not _valid_date(s):
        raise ApiError(400, "%s 는 YYYY-MM-DD 형식이어야 합니다" % what)
    return s


def _num(body, key, required=True):
    """본문에서 숫자(epoch 등) 꺼내기. bool 은 거부."""
    if key not in body or body[key] is None:
        if required:
            raise ApiError(400, "'%s' 값이 필요합니다" % key)
        return None
    v = body[key]
    if isinstance(v, bool):
        raise ApiError(400, "'%s' 는 숫자여야 합니다" % key)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "'%s' 는 숫자여야 합니다" % key)
    if not math.isfinite(f):
        # json.loads 는 Infinity/NaN 리터럴을 받아들이므로 여기서 막지 않으면 DB 에 저장돼
        # 이후 모든 /api/day 응답에 Infinity 가 섞여 브라우저 JSON.parse 가 깨진다.
        raise ApiError(400, "'%s' 는 유한한 숫자여야 합니다" % key)
    return f


def _load_rules_locked():
    """rules.json 읽기도 _rules_lock 안에서: Windows 는 열려 있는 파일을 os.replace 못 하므로
    저장(api_rule)과 읽기(api_day/_status)가 겹치면 PermissionError 가 난다."""
    with _rules_lock:
        return common.load_rules()


def _save_rules_retry(rules, tries=5, wait=0.02):
    """외부 프로그램(편집기 등)이 rules.json 을 잠깐 열어 둔 경우를 흡수."""
    last = None
    for i in range(tries):
        try:
            common.save_rules(rules)
            return
        except PermissionError as e:
            last = e
            time.sleep(wait * (i + 1))
    raise last


def _int(body, key, required=True, minimum=None):
    if key not in body or body[key] is None:
        if required:
            raise ApiError(400, "'%s' 값이 필요합니다" % key)
        return None
    v = body[key]
    if isinstance(v, bool):
        raise ApiError(400, "'%s' 는 정수여야 합니다" % key)
    try:
        iv = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "'%s' 는 정수여야 합니다" % key)
    if minimum is not None and iv < minimum:
        raise ApiError(400, "'%s' 는 %d 이상이어야 합니다" % (key, minimum))
    return iv


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _classify(exe_base_name, title):
    """engine.classify 가 있으면 사용, 없으면 'unknown'."""
    if engine is None or not hasattr(engine, "classify"):
        return "unknown"
    try:
        res = engine.classify(exe_base_name, title, _load_rules_locked())
        cat = res[0] if isinstance(res, (tuple, list)) else res
        return cat if cat in common.CAT else "unknown"
    except Exception:
        log.exception("engine.classify 실패")
        return "unknown"


def _paused_until(conn, now):
    v = common.get_state(conn, "paused_until")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > now else None


def _status(conn, now, names=None):
    """GET /api/status 응답 본문. /api/day 에서도 재사용.
    current 에는 §15 표시 이름 name 도 넣는다(헤더 상태 배너 툴팁용). names 가 None 이면 names.json 을 읽는다."""
    row = conn.execute(
        "SELECT * FROM sessions ORDER BY start_ts DESC LIMIT 1"
    ).fetchone()
    alive = False
    last_seen = None
    seconds_since = None
    if row is not None:
        last_seen = float(row["last_seen"])
        seconds_since = int(max(0.0, now - last_seen))
        dead_after = float(CFG.get("dead_after_sec", 120))
        alive = row["end_ts"] is None and (now - last_seen) < dead_after
    current = None
    if alive:
        ev = conn.execute(
            "SELECT exe, title FROM events WHERE kind='app' "
            "ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        if ev is not None:
            base = common.exe_base(ev["exe"] or "")
            title = ev["title"] or ""
            current = {"exe": base, "title": title, "category": _classify(base, title)}
            if engine is not None:
                if names is None:
                    names = _load_names_locked()
                current["name"] = engine.display_name(base, title, names)
    return {
        "now": now,
        "alive": alive,
        "last_seen": last_seen,
        "seconds_since": seconds_since,
        "paused_until": _paused_until(conn, now),
        "current": current,
    }


# ------------------------------------------------------------------ API 핸들러
# 시그니처: fn(conn, query(dict of list), body(dict)) -> JSON 직렬화 가능한 객체

def api_status(conn, query, body):
    return _status(conn, common.now_ts())


def api_config(conn, query, body):
    return CFG


def _state_prefix_events(conn, sessions, lo):
    """window.start-6h(lo) 이전에 일어난 이벤트 중 상태 복원에 필요한 것만 골라 앞에 붙인다.

    PC 를 켜둔 채 트래커가 밤새 살아 있으면(전날 19:00 잠금 → 22:00~04:00 이벤트 0건) lo 이후
    이벤트만으로는 04:00 시점 상태(잠금/idle/전경창)를 알 수 없어 04:00~첫 이벤트가 '미분류' 가 된다.
    kind 그룹(start|stop, app, lock|unlock, idle_on|idle_off, remote_on|remote_off, pause|resume)별로
    lo 이전 마지막 1건씩만 가져온다 — 엔진은 (ts,id) 순으로 재생하므로 start/stop 의 reset 이
    그보다 앞선 이벤트를 자연히 지운다. 겹치는 세션들의 가장 이른 start_ts 보다 앞선 것은 제외."""
    starts = [float(s["start_ts"]) for s in sessions if s.get("start_ts") is not None]
    if not starts or min(starts) >= lo:
        return []
    floor = min(starts)
    out = []
    for kinds in _STATE_KIND_GROUPS:
        # kind 하나씩 LIMIT 1 로 뽑아 (ts,id) 가 가장 늦은 것을 고른다. `kind IN (...)` 한 방 쿼리는
        # 두 kind 그룹에서 정렬용 TEMP B-TREE 가 생겨 (floor, lo) 구간의 해당 kind 행을 전부 읽고,
        # idx_events_kind_ts 가 없으면(구 DB) lo 직전에 드문 kind 는 세션 시작까지 전체 역방향 스캔이
        # 되어 /api/range 62일이 수 초를 넘긴다. kind 별 조회는 인덱스가 있으면 O(log n) 이다.
        best = None
        for kind in kinds:
            row = conn.execute(
                "SELECT * FROM events WHERE kind = ? AND ts >= ? AND ts < ? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (kind, floor, lo),
            ).fetchone()
            if row is not None and (best is None or
                                    (float(row["ts"]), int(row["id"])) > (float(best["ts"]), int(best["id"]))):
                best = row
        if best is not None:
            out.append(dict(best))
    out.sort(key=lambda e: (float(e["ts"]), int(e["id"])))
    return out


def ensure_indexes(conn):
    """common.SCHEMA(수정 금지)에 없는 보조 인덱스를 서버 쪽에서 보장한다(멱등).

    events(kind, ts): _state_prefix_events 의 kind 별 '마지막 1건' 조회용. idx_events_ts(ts) 만 있으면
    lo 직전에 거의 없는 kind(pause/remote/start 등)는 세션 시작(floor)까지 전부 거슬러 읽어, 트래커가
    몇 주 살아 있을 때 /api/range 62일이 수 초를 넘긴다(§11.2 '수 초 내' 위반). 설계 소유자에게
    §5 SCHEMA 반영을 요청해 두었고, 그때까지는 여기서 만든다."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts)")
    conn.commit()


def _display_config():
    return {
        "display_start_hour": CFG.get("display_start_hour", 6),
        "display_end_hour": CFG.get("display_end_hour", 24),
        "day_start_hour": CFG.get("day_start_hour", 4),
        "night_start_hour": CFG.get("night_start_hour", 20),
        "night_prompt_hour": CFG.get("night_prompt_hour", 16),
        "night_band_end_hour": CFG.get("night_band_end_hour", 23),
        "budget_card": bool(CFG.get("budget_card", False)),
        "anki_card": bool(CFG.get("anki_card", False)),
    }


def _load_names_locked():
    """names.json 도 규칙과 같은 락 안에서 읽는다(§15.3)."""
    with _rules_lock:
        return common.load_names()


def compute_day(conn, date_str, now, rules=None, names=None):
    """한 날짜의 build_day 결과(+ "overrides": 겹치는 정정 목록). /api/day 와 /api/range 가 공유.
    names(§15): 표시 이름 규칙. None 이면 names.json 을 읽는다.

    §8: (window.start-6h, window.end) 이벤트 + 겹치는 세션·override 를 읽고, window.start-6h 이전
    상태 복원용 이벤트(_state_prefix_events)를 앞에 붙여 engine.build_day 를 호출한다(캐시 없음).
    오늘 이후 날짜는 DB 를 읽지 않고 빈 이벤트로 build_day 를 호출한다(spans=[], summary 기본값).
    summary 필드는 엔진이 주는 그대로 통과시킨다(서버가 필드를 만들지 않음)."""
    if engine is None:
        raise ApiError(500, "engine 모듈을 불러올 수 없습니다: %s" % ENGINE_ERROR)
    try:
        ws, we = common.day_window(date_str, CFG)
    except (OSError, OverflowError, ValueError):
        raise ApiError(400, "지원하지 않는 날짜입니다: %s" % date_str)
    if rules is None:
        rules = _load_rules_locked()
    if names is None:
        names = _load_names_locked()
    if date_str > common.today_str(CFG, now):
        events, sessions, overrides = [], [], []
    else:
        lo = ws - 6 * 3600
        hb = float(CFG.get("heartbeat_sec", 30))
        sessions = _rows(
            conn,
            "SELECT * FROM sessions WHERE start_ts < ? "
            "AND COALESCE(end_ts, last_seen) + ? > ? ORDER BY start_ts, id",
            (we, hb, lo),
        )
        events = _rows(
            conn,
            "SELECT * FROM events WHERE ts >= ? AND ts < ? ORDER BY ts, id",
            (lo, we),
        )
        events = _state_prefix_events(conn, sessions, lo) + events
        overrides = _rows(
            conn,
            "SELECT * FROM overrides WHERE start_ts < ? AND end_ts > ? "
            "ORDER BY created_ts, id",
            (we, ws),
        )
    result = engine.build_day(date_str, events, sessions, overrides, rules, CFG, now, names=names)
    if not isinstance(result, dict):
        result = dict(result)
    result["overrides"] = [
        {
            "id": o["id"],
            "start_ts": o["start_ts"],
            "end_ts": o["end_ts"],
            "label": o["label"],
            "note": o.get("note") or "",
        }
        for o in overrides
    ]
    return result


def api_day(conn, query, body):
    now = common.now_ts()
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG, now)
    _require_date(date)
    result = compute_day(conn, date, now)
    result["categories"] = common.CATEGORIES
    result["config"] = _display_config()
    result["status"] = _status(conn, now)
    result["night"] = _night_for_day(conn, date, result.get("spans") or [])
    return result


RANGE_MAX_DAYS = 62
NIGHT_ASK_DAYS = 3   # §19.8 다음날 확인은 최근 3일치만 묻는다


def _date_add(date_str, days):
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d") + datetime.timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def api_range(conn, query, body):
    """GET /api/range?start=D1&end=D2&spans=0|1 — §11.2. days 는 날짜 오름차순, 둘 다 포함."""
    now = common.now_ts()
    start = (query.get("start") or [""])[0].strip()
    end = (query.get("end") or [""])[0].strip()
    _require_date(start, "start")
    _require_date(end, "end")
    if start > end:
        raise ApiError(400, "start 는 end 보다 늦을 수 없습니다")
    d0 = datetime.datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.datetime.strptime(end, "%Y-%m-%d")
    ndays = (d1 - d0).days + 1
    if ndays > RANGE_MAX_DAYS:
        raise ApiError(400, "기간은 최대 %d일까지입니다 (요청: %d일)" % (RANGE_MAX_DAYS, ndays))
    with_spans = (query.get("spans") or ["0"])[0].strip() in ("1", "true", "yes")
    log.debug("range %s~%s days=%d spans=%d", start, end, ndays, 1 if with_spans else 0)

    rules = _load_rules_locked()
    names = _load_names_locked()
    days = []
    for i in range(ndays):
        date = _date_add(start, i)
        r = compute_day(conn, date, now, rules, names)
        item = {"date": r.get("date", date), "window": r.get("window"), "summary": r.get("summary")}
        if with_spans:
            item["spans"] = r.get("spans", [])
        days.append(item)
    return {
        "start": start,
        "end": end,
        "days": days,
        "categories": common.CATEGORIES,
        "config": _display_config(),
        "status": _status(conn, now),
    }


# ------------------------------------------------------------------ 재시작
_SERVER = None  # main() 에서 Server 인스턴스를 넣는다 (api_restart 가 리스닝 소켓을 닫기 위해)
RESTART_DELAY_SEC = 0.5
# 새 서버가 곧바로 죽는지 지켜보는 시간. UI 는 응답 3초 뒤 새로고침하므로 0.5(지연)+1.0(감시)+≤0.5(새 서버
# 바인드 재시도 간격) 안에 새 서버가 떠야 한다.
RESTART_CHILD_WATCH_SEC = 1.0
# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
RESTART_CREATIONFLAGS = 0x8 | 0x200 | 0x08000000


def _pythonw_path():
    exe = sys.executable or ""
    d = os.path.dirname(exe)
    cand = os.path.join(d, "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    return exe or "pythonw"


def _restart_worker():
    """응답이 나간 뒤 별도 스레드: 0.5초 대기 → 새 server.py 를 DETACHED 로 띄움(현재 실행 인자
    sys.argv[1:] 그대로) → 잠깐(RESTART_CHILD_WATCH_SEC) 살아 있는지 확인 → 리스닝 소켓 close → os._exit(0).

    §11.2 의 '소켓 close → Popen' 순서를 'Popen → 소켓 close' 로 바꿨다: Popen 이 실패하거나 새 서버가
    곧바로 죽으면(server.py 문법 오류 등) 소켓을 닫지 않고 현재 프로세스를 그대로 살려 두어 'UI 서버 0개'
    상태를 피하기 위함. 새 서버는 어차피 포트가 비기까지 0.5초 간격으로 최대 10초 바인드를 재시도하므로
    순서를 바꿔도 동작은 같다(재시작이 0.5~1초 늦어질 뿐)."""
    import subprocess
    time.sleep(RESTART_DELAY_SEC)
    script = os.path.abspath(__file__)
    cmd = [_pythonw_path(), script] + list(sys.argv[1:])
    try:
        p = subprocess.Popen(
            cmd, cwd=ROOT,
            creationflags=RESTART_CREATIONFLAGS,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log.info("재시작: 새 서버 실행 pid=%s cmd=%s", p.pid, cmd)
    except Exception:
        log.exception("재시작: 새 서버 실행 실패 — 현재 서버를 그대로 유지")
        return
    # 새 서버가 바로 죽으면(문법 오류·DB 열기 실패 등) 현재 서버를 유지한다. 정상이면 포트가 비기를
    # 기다리며 살아 있으므로(바인드 재시도 루프) 이 시간 안에는 끝나지 않는다.
    deadline = time.time() + RESTART_CHILD_WATCH_SEC
    while time.time() < deadline:
        rc = p.poll()
        if rc is not None:
            log.error("재시작: 새 서버가 곧바로 종료됨(exit=%s) — 현재 서버를 그대로 유지. "
                      "server.log 에서 원인을 확인하세요", rc)
            return
        time.sleep(0.1)
    srv = _SERVER
    try:
        if srv is not None:
            srv.server_close()
            log.info("재시작: 리스닝 소켓 닫음")
    except Exception:
        log.exception("재시작: 소켓 close 실패(계속 진행)")
    log.info("재시작: 현재 프로세스 종료 (os._exit)")
    for h in list(log.handlers):
        try:
            h.flush()
        except Exception:
            pass
    os._exit(0)


def _start_restart_thread():
    threading.Thread(target=_restart_worker, name="restart", daemon=False).start()


def api_restart(conn, query, body):
    """POST /api/restart — {"ok": true} 를 먼저 완전히 보낸 뒤(핸들러가 after_send 훅 호출) 재시작."""
    log.info("재시작 요청 받음 (args=%s)", sys.argv[1:])
    return {"ok": True}


api_restart.after_send = _start_restart_thread


def api_override(conn, query, body):
    start = _num(body, "start")
    end = _num(body, "end")
    if not (end > start):
        raise ApiError(400, "end 는 start 보다 커야 합니다")
    if end - start > 2 * 86400:
        raise ApiError(400, "정정 구간은 이틀을 넘을 수 없습니다")
    label = body.get("label")
    if label not in common.MANUAL_CATEGORIES:
        raise ApiError(400, "label 은 %s 중 하나여야 합니다" % ", ".join(common.MANUAL_CATEGORIES))
    note = body.get("note")
    note = "" if note is None else str(note)
    cur = conn.execute(
        "INSERT INTO overrides(start_ts, end_ts, label, note, created_ts) VALUES(?,?,?,?,?)",
        (start, end, label, note, common.now_ts()),
    )
    conn.commit()
    log.info("override 추가 id=%s %s %.0f-%.0f", cur.lastrowid, label, start, end)
    return {"id": cur.lastrowid}


def api_override_delete(conn, query, body):
    oid = _int(body, "id")
    conn.execute("DELETE FROM overrides WHERE id=?", (oid,))
    conn.commit()
    log.info("override 삭제 id=%s", oid)
    return {"ok": True}


def api_rules(conn, query, body):
    with _rules_lock:
        return {"rules": common.load_rules_raw()}


def api_names(conn, query, body):
    """GET /api/names — names.json 원본 JSON (§15.3). 편집 UI 없음(파일 편집)."""
    with _rules_lock:
        return common.load_names_raw()


def api_rule(conn, query, body):
    target = body.get("target")
    if target not in ("exe", "title"):
        raise ApiError(400, "target 은 'exe' 또는 'title' 이어야 합니다")
    pattern = body.get("pattern")
    if not isinstance(pattern, str):
        raise ApiError(400, "pattern 은 문자열이어야 합니다")
    pattern = pattern.strip()
    if not pattern:
        raise ApiError(400, "pattern 이 비어 있습니다")
    category = body.get("category")
    if category not in common.CAT:
        raise ApiError(400, "category 는 %s 중 하나여야 합니다" % ", ".join(common.CAT.keys()))
    is_regex = bool(body.get("is_regex", False))
    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ApiError(400, "정규식 오류: %s" % e)
    idle_limit = _int(body, "idle_limit_sec", required=False, minimum=0)

    with _rules_lock:
        rules = common.load_rules_raw()
        found = None
        for r in rules:
            if r.get("target") == target and r.get("pattern") == pattern:
                found = r
                break
        prio = UI_RULE_PRIORITY[target]
        if found is not None:
            found["category"] = category
            if "idle_limit_sec" in body:
                found["idle_limit_sec"] = idle_limit
            if "is_regex" in body:
                found["is_regex"] = is_regex
            found["enabled"] = True
            # 사용자가 다시 지정한 규칙이 기본 규칙에 져서 '안 바뀌는' 일이 없게 priority 도 끌어올린다
            try:
                if int(found.get("priority", 100)) < prio:
                    found["priority"] = prio
            except (TypeError, ValueError):
                found["priority"] = prio
            action = "갱신"
        else:
            rules.append({
                "target": target,
                "pattern": pattern,
                "is_regex": is_regex,
                "category": category,
                "priority": prio,
                "idle_limit_sec": idle_limit,
                "enabled": True,
                "note": "UI에서 추가",
            })
            action = "추가"
        _save_rules_retry(rules)
    log.info("규칙 %s target=%s pattern=%r category=%s", action, target, pattern, category)
    return {"ok": True, "rules": rules}


def api_rule_delete(conn, query, body):
    target = body.get("target")
    pattern = body.get("pattern")
    if target not in ("exe", "title") or not isinstance(pattern, str) or not pattern:
        raise ApiError(400, "target 과 pattern 이 필요합니다")
    with _rules_lock:
        rules = common.load_rules_raw()
        kept = [r for r in rules if not (r.get("target") == target and r.get("pattern") == pattern)]
        removed = len(rules) - len(kept)
        if removed:
            _save_rules_retry(kept)
    log.info("규칙 삭제 target=%s pattern=%r removed=%d", target, pattern, removed)
    return {"ok": True, "removed": removed}


def _todo_row(r):
    return {
        "id": r["id"],
        "text": r["text"],
        "day": r["day"],
        "done": int(r["done"] or 0),
        "doing": int(r["doing"] or 0) if "doing" in r.keys() else 0,  # §20: todo_progress 행이 있으면 1
        "created_ts": r["created_ts"],
        "done_ts": r["done_ts"],
    }


def _next_sort_order(conn, day):
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM todos WHERE day=?", (day,)
    ).fetchone()
    return int(row[0])


def api_todos_get(conn, query, body):
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG)
    _require_date(date)
    # §20: 진행 중 → 대기 → 완료 순. doing 은 todo_progress 행 유무
    today = [_todo_row(r) for r in conn.execute(
        "SELECT t.*, (p.todo_id IS NOT NULL) AS doing FROM todos t "
        "LEFT JOIN todo_progress p ON p.todo_id=t.id "
        "WHERE t.day=? ORDER BY doing DESC, t.done ASC, t.sort_order, t.id", (date,)
    ).fetchall()]
    carried = [_todo_row(r) for r in conn.execute(
        "SELECT t.*, (p.todo_id IS NOT NULL) AS doing FROM todos t "
        "LEFT JOIN todo_progress p ON p.todo_id=t.id "
        "WHERE t.day<? AND t.done=0 ORDER BY doing DESC, t.day, t.sort_order, t.id", (date,)
    ).fetchall()]
    return {"today": today, "carried": carried}


def api_todos_add(conn, query, body):
    text = body.get("text")
    text = "" if text is None else str(text).strip()
    if not text:
        raise ApiError(400, "text 가 비어 있습니다")
    day = body.get("day") or common.today_str(CFG)
    _require_date(day, "day")
    cur = conn.execute(
        "INSERT INTO todos(text, day, done, created_ts, done_ts, sort_order) "
        "VALUES(?,?,0,?,NULL,?)",
        (text, day, common.now_ts(), _next_sort_order(conn, day)),
    )
    conn.commit()
    return {"id": cur.lastrowid}


def api_todos_update(conn, query, body):
    tid = _int(body, "id")
    row = conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 할 일 id=%d" % tid)
    sets = []
    params = []
    done = None
    if "done" in body:
        d = body.get("done")
        done = 1 if (d is True or d == 1 or d == "1" or d == "true") else 0
        sets.append("done=?")
        params.append(done)
        sets.append("done_ts=?")
        params.append(common.now_ts() if done else None)
    if "doing" in body:
        # §20: 진행 토글. 행이 있으면 진행 중. 완료 요청과 같이 오면 완료가 이긴다
        g = body.get("doing")
        doing = 1 if (g is True or g == 1 or g == "1" or g == "true") else 0
        if doing and not done:
            conn.execute("INSERT OR IGNORE INTO todo_progress(todo_id, started_ts) VALUES(?,?)",
                         (tid, common.now_ts()))
        else:
            conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (tid,))
        conn.commit()
    if done:
        conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (tid,))
        conn.commit()
    if "text" in body:
        text = body.get("text")
        text = "" if text is None else str(text).strip()
        if not text:
            raise ApiError(400, "text 가 비어 있습니다")
        sets.append("text=?")
        params.append(text)
    if "day" in body:
        day = body.get("day")
        _require_date(day, "day")
        if day != row["day"]:
            sets.append("day=?")
            params.append(day)
            sets.append("sort_order=?")
            params.append(_next_sort_order(conn, day))
    if sets:
        params.append(tid)
        conn.execute("UPDATE todos SET %s WHERE id=?" % ", ".join(sets), params)
        conn.commit()
    return {"ok": True}


def api_todos_delete(conn, query, body):
    tid = _int(body, "id")
    conn.execute("DELETE FROM todos WHERE id=?", (tid,))
    conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (tid,))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 밤 일정 (§19)
def _night_row(r):
    k = common.NIGHT_KIND.get(r["kind"]) or {}
    return {
        "id": r["id"], "day": r["day"], "kind": r["kind"],
        "kind_name": k.get("name", r["kind"]), "ask": bool(k.get("ask", False)),
        "note": r["note"], "created_ts": r["created_ts"],
        "done": int(r["done"] or 0), "checked_ts": r["checked_ts"],
    }


def _night_of(conn, day):
    r = conn.execute(
        "SELECT * FROM night WHERE day=? ORDER BY id DESC LIMIT 1", (day,)
    ).fetchone()
    return _night_row(r) if r is not None else None


def _night_workout(conn, date):
    """마지막으로 '했음'이 확정된 운동일 기준 격일 판정. 미확인 계획은 세지 않는다(§19.6)."""
    row = conn.execute(
        "SELECT MAX(day) AS d FROM night WHERE kind='workout' AND done=1 AND day<=?",
        (date,),
    ).fetchone()
    last = row["d"] if row is not None else None
    interval = int(CFG.get("workout_interval_days", 2))
    if not last:
        return {"last": None, "gap": None, "due": True, "interval_days": interval}
    a = datetime.datetime.strptime(date, "%Y-%m-%d").date().toordinal()
    b = datetime.datetime.strptime(last, "%Y-%m-%d").date().toordinal()
    gap = a - b
    return {"last": last, "gap": gap, "due": gap >= interval, "interval_days": interval}


def _night_for_day(conn, date, spans):
    """/api/day 에 실을 값. 연구는 묻지 않고 spans 로 측정한다(§19.6)."""
    n = _night_of(conn, date)
    if n is None:
        return None
    start, end = common.night_window(date, CFG)
    n["start"] = start
    n["end"] = end
    if n["kind"] == "research":
        sec = engine.night_research_sec(spans, start, end)
        n["research_sec"] = round(sec, 1)
        n["done"] = 1 if sec >= float(CFG.get("night_research_min_sec", 1800)) else 0
        n["checked_ts"] = None      # 측정값이라 사람이 확인한 시각이 없다
    return n


def api_night_get(conn, query, body):
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG)
    _require_date(date)
    since = (datetime.datetime.strptime(date, "%Y-%m-%d").date()
             - datetime.timedelta(days=NIGHT_ASK_DAYS)).strftime("%Y-%m-%d")
    marks = ",".join("?" for _ in common.NIGHT_ASK_KINDS)
    pending = [_night_row(r) for r in conn.execute(
        "SELECT * FROM night WHERE day<? AND day>=? AND checked_ts IS NULL "
        "AND kind IN (%s) ORDER BY day DESC, id DESC" % marks,
        tuple([date, since] + list(common.NIGHT_ASK_KINDS)),
    ).fetchall()]
    return {
        "date": date,
        "today": _night_of(conn, date),
        "pending": pending,
        "workout": _night_workout(conn, date),
        "kinds": common.NIGHT_KINDS,
    }


def api_night_set(conn, query, body):
    kind = body.get("kind")
    kind = "" if kind is None else str(kind).strip()
    if kind not in common.NIGHT_KIND:
        raise ApiError(400, "kind 는 %s 중 하나여야 합니다"
                       % ", ".join(k["id"] for k in common.NIGHT_KINDS))
    day = body.get("day") or common.today_str(CFG)
    _require_date(day, "day")
    note = body.get("note")
    note = "" if note is None else str(note).strip()[:300]
    now = common.now_ts()
    row = conn.execute(
        "SELECT * FROM night WHERE day=? ORDER BY id DESC LIMIT 1", (day,)
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO night(day, kind, note, created_ts, done, checked_ts) "
            "VALUES(?,?,?,?,0,NULL)", (day, kind, note or None, now))
        nid = cur.lastrowid
    else:
        nid = row["id"]
        if row["kind"] != kind:   # 다른 일정으로 바꿨으면 이전 확인은 무효
            conn.execute("UPDATE night SET kind=?, note=?, done=0, checked_ts=NULL WHERE id=?",
                         (kind, note or None, nid))
        else:
            conn.execute("UPDATE night SET note=? WHERE id=?", (note or None, nid))
        conn.execute("DELETE FROM night WHERE day=? AND id<>?", (day, nid))
    # §23.1: '다른 걸 했음' — 종류를 바꾸면서 이행까지 한 번에. 물어보는 종류일 때만 뜻이 있다
    if body.get("done") is not None and common.NIGHT_KIND[kind].get("ask"):
        d = body.get("done")
        done = 1 if (d is True or d == 1 or d == "1" or d == "true") else 0
        conn.execute("UPDATE night SET done=?, checked_ts=? WHERE id=?", (done, now, nid))
    conn.commit()
    return {"id": nid}


def api_night_confirm(conn, query, body):
    nid = _int(body, "id")
    row = conn.execute("SELECT * FROM night WHERE id=?", (nid,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 밤 일정 id=%d" % nid)
    k = common.NIGHT_KIND.get(row["kind"]) or {}
    if not k.get("ask"):
        raise ApiError(400, "'%s' 는 확인해서 기록하는 종류가 아닙니다" % k.get("name", row["kind"]))
    d = body.get("done")
    done = 1 if (d is True or d == 1 or d == "1" or d == "true") else 0
    conn.execute("UPDATE night SET done=?, checked_ts=? WHERE id=?",
                 (done, common.now_ts(), nid))
    conn.commit()
    return {"ok": True}


def api_night_delete(conn, query, body):
    nid = _int(body, "id")
    conn.execute("DELETE FROM night WHERE id=?", (nid,))
    conn.commit()
    return {"ok": True}


# ── 예산 (§21) ──────────────────────────────────────────────
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _require_month(s):
    if not isinstance(s, str) or not _MONTH_RE.match(s):
        raise ApiError(400, "month 는 YYYY-MM 형식이어야 합니다")
    return s


def _spend_row(r):
    return {"id": r["id"], "day": r["day"], "amount": int(r["amount"] or 0),
            "category": r["category"] or "", "note": r["note"] or "", "created_ts": r["created_ts"]}


def _plan_of(conn, month):
    return [{"category": r["category"], "amount": int(r["amount"] or 0)} for r in conn.execute(
        "SELECT category, amount FROM budget_plan WHERE month=? ORDER BY sort_order, id", (month,)).fetchall()]


def api_budget_get(conn, query, body):
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG)
    _require_date(date)
    month = date[:7]
    plan = _plan_of(conn, month)
    spent = {}
    for r in conn.execute(
            "SELECT category, SUM(amount) AS s FROM spend WHERE substr(day,1,7)=? GROUP BY category", (month,)):
        spent[r["category"] or ""] = int(r["s"] or 0)
    rows = []
    for p in plan:
        sp = spent.pop(p["category"], 0)
        rows.append({"category": p["category"], "amount": p["amount"], "spent": sp, "left": p["amount"] - sp})
    other = sum(spent.values())          # 배분에 없는 항목(빈 항목 포함)에 쓴 돈
    if other:
        rows.append({"category": "기타", "amount": 0, "spent": other, "left": -other, "extra": True})
    total_amount = sum(p["amount"] for p in plan)
    total_spent = sum(r["spent"] for r in rows)
    day = [_spend_row(r) for r in conn.execute("SELECT * FROM spend WHERE day=? ORDER BY id", (date,)).fetchall()]
    prev = []
    if not plan:
        r = conn.execute("SELECT month FROM budget_plan WHERE month<? ORDER BY month DESC LIMIT 1", (month,)).fetchone()
        if r:
            prev = _plan_of(conn, r["month"])
    return {"month": month, "plan": rows,
            "total": {"amount": total_amount, "spent": total_spent, "left": total_amount - total_spent},
            "day": day, "prev": prev}


def api_budget_plan_set(conn, query, body):
    month = _require_month(body.get("month"))
    items = body.get("items")
    if not isinstance(items, list):
        raise ApiError(400, "items 는 목록이어야 합니다")
    clean, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            raise ApiError(400, "items 의 원소는 {category, amount} 여야 합니다")
        cat = str(it.get("category") or "").strip()[:40]
        if not cat:
            raise ApiError(400, "항목 이름이 비어 있습니다")
        if cat == "기타":
            raise ApiError(400, "'기타' 는 배분에 없는 지출을 모으는 이름이라 항목으로 쓸 수 없습니다")
        if cat in seen:
            raise ApiError(400, "항목 이름이 겹칩니다: %s" % cat)
        seen.add(cat)
        amt = _int(it, "amount", minimum=0)
        clean.append((cat, amt))
    now = common.now_ts()
    conn.execute("DELETE FROM budget_plan WHERE month=?", (month,))
    for i, (cat, amt) in enumerate(clean):
        conn.execute("INSERT INTO budget_plan(month, category, amount, sort_order, created_ts) VALUES(?,?,?,?,?)",
                     (month, cat, amt, i, now))
    conn.commit()
    return {"ok": True, "n": len(clean)}


def _spend_fields(body, row=None):
    """amount 는 정수 또는 text('9000 점심') 에서 파싱. 돌려주는 값은 (amount, category, note)."""
    amount = None
    note = body.get("note")
    if body.get("text") is not None and "amount" not in body:
        amount, rest = common.parse_amount(str(body.get("text")))
        if amount is None:
            raise ApiError(400, "금액을 찾지 못했습니다. '9000 점심' 처럼 적어 주세요")
        if note is None:
            note = rest
    elif "amount" in body:
        amount = _int(body, "amount", minimum=1)
    elif row is None:
        raise ApiError(400, "amount 나 text 가 필요합니다")
    if amount is not None and amount < 1:
        raise ApiError(400, "금액은 1원 이상이어야 합니다")
    cat = body.get("category")
    cat = None if cat is None else str(cat).strip()[:40]
    note = None if note is None else str(note).strip()[:200]
    return amount, cat, note


def api_spend_add(conn, query, body):
    day = body.get("day") or common.today_str(CFG)
    _require_date(day, "day")
    amount, cat, note = _spend_fields(body)
    cur = conn.execute("INSERT INTO spend(day, amount, category, note, created_ts) VALUES(?,?,?,?,?)",
                       (day, amount, cat or None, note or None, common.now_ts()))
    conn.commit()
    return {"id": cur.lastrowid}


def api_spend_update(conn, query, body):
    sid = _int(body, "id")
    row = conn.execute("SELECT * FROM spend WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 지출 id=%d" % sid)
    amount, cat, note = _spend_fields(body, row)
    sets, params = [], []
    if amount is not None:
        sets.append("amount=?"); params.append(amount)
    if cat is not None:
        sets.append("category=?"); params.append(cat or None)
    if note is not None:
        sets.append("note=?"); params.append(note or None)
    if "day" in body:
        day = body.get("day")
        _require_date(day, "day")
        sets.append("day=?"); params.append(day)
    if sets:
        params.append(sid)
        conn.execute("UPDATE spend SET %s WHERE id=?" % ", ".join(sets), params)
        conn.commit()
    return {"ok": True}


def api_spend_delete(conn, query, body):
    sid = _int(body, "id")
    conn.execute("DELETE FROM spend WHERE id=?", (sid,))
    conn.commit()
    return {"ok": True}


# ── 대학원 학사일정 (§24) ─────────────────────────────────────
def api_schedule_get(conn, query, body):
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG)
    _require_date(date)
    if academic_schedule is None:
        return {"date": date, "items": [], "fetched": 0, "error": SCHEDULE_ERROR, "source": ""}
    return academic_schedule.api_get(date, CFG)


# ── Anki 복습 모니터 (§31) ─────────────────────────────────────
def api_anki_get(conn, query, body):
    if anki_stats is None:
        return {"available": False, "reason": ANKI_ERROR, "data": None}
    force = (query.get("force") or ["0"])[0] in ("1", "true")
    return anki_stats.snapshot(force=force)


# ── 내 일정·마감 (§28) ─────────────────────────────────────────
def api_events_get(conn, query, body):
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG)
    _require_date(date)
    if my_events is None:
        return {"date": date, "items": [], "error": EVENTS_ERROR}
    return {"date": date, "items": my_events.upcoming(conn, date, cfg=CFG),
            "claude": my_events._find_claude() is not None}


def api_events_add(conn, query, body):
    if my_events is None:
        raise ApiError(500, "일정 모듈이 없습니다: %s" % EVENTS_ERROR)
    day = body.get("day")
    if day:
        _require_date(day, "day")
    try:
        eid, parsing = my_events.add(conn, body.get("text"), day or None, CFG)
    except ValueError:
        raise ApiError(400, "글이 비어 있습니다")
    return {"id": eid, "parsing": parsing}


def api_events_update(conn, query, body):
    eid = _int(body, "id")
    row = conn.execute("SELECT * FROM plans WHERE id=?", (eid,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 일정 id=%d" % eid)
    sets, params = [], []
    if "title" in body:
        t = str(body.get("title") or "").strip()
        if not t:
            raise ApiError(400, "제목이 비어 있습니다")
        sets.append("title=?"); params.append(t[:60])
    if "day" in body:
        d = body.get("day")
        if d:
            _require_date(d, "day")
            sets += ["day=?", "confidence=?", "status=?"]; params += [d, "confirmed", "ready"]   # 사람이 적은 날짜가 최종
        else:
            sets += ["day=?"]; params += [None]
    if "kind" in body:
        if body.get("kind") not in my_events.KINDS:
            raise ApiError(400, "kind 는 deadline|task|event")
        sets.append("kind=?"); params.append(body["kind"])
    if "repeat" in body:
        sets.append("repeat=?"); params.append("weekly" if body.get("repeat") == "weekly" else "")
    if "note" in body:
        sets.append("note=?"); params.append(str(body.get("note") or "")[:300])
    if "skip_until" in body:
        su = body.get("skip_until")
        if su:
            _require_date(su, "skip_until")
        sets.append("skip_until=?"); params.append(su or None)
    if "done" in body:
        d = body.get("done")
        done = 1 if (d is True or d == 1 or d == "1" or d == "true") else 0
        sets += ["done=?", "done_ts=?"]; params += [done, common.now_ts() if done else None]
    if sets:
        sets.append("updated_ts=?"); params.append(common.now_ts())
        params.append(eid)
        conn.execute("UPDATE plans SET %s WHERE id=?" % ", ".join(sets), params)
        conn.commit()
    return {"ok": True}


def api_events_delete(conn, query, body):
    eid = _int(body, "id")
    conn.execute("DELETE FROM plans WHERE id=?", (eid,))
    conn.commit()
    return {"ok": True}


def api_events_retry(conn, query, body):
    """날짜를 못 찾았거나 실패한 행을 다시 가른다."""
    eid = _int(body, "id")
    row = conn.execute("SELECT * FROM plans WHERE id=?", (eid,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 일정 id=%d" % eid)
    conn.execute("UPDATE plans SET status='parsing', updated_ts=? WHERE id=?", (common.now_ts(), eid))
    conn.commit()
    today = common.today_str(CFG)
    if my_events.ASYNC:
        threading.Thread(target=my_events.resolve, args=(eid, row["text"], today, CFG), daemon=True).start()
    else:
        my_events.resolve(eid, row["text"], today, CFG, conn=conn)
    return {"ok": True}


# ── 기간 판 (§23.3) ──────────────────────────────────────────
def api_period_get(conn, query, body):
    start = (query.get("start") or [""])[0].strip()
    end = (query.get("end") or [""])[0].strip()
    _require_date(start, "start")
    _require_date(end, "end")
    if start > end:
        raise ApiError(400, "start 는 end 보다 늦을 수 없습니다")
    d0 = datetime.datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.datetime.strptime(end, "%Y-%m-%d")
    ndays = (d1 - d0).days + 1
    if ndays > RANGE_MAX_DAYS:
        raise ApiError(400, "기간은 최대 %d일까지입니다 (요청: %d일)" % (RANGE_MAX_DAYS, ndays))
    nights = {}
    for r in conn.execute("SELECT * FROM night WHERE day>=? AND day<=? ORDER BY day, id", (start, end)).fetchall():
        nights[r["day"]] = r          # 하루 한 행(§19.6) — 마지막 것이 남는다
    spend = {}
    for r in conn.execute("SELECT day, SUM(amount) AS s FROM spend WHERE day>=? AND day<=? GROUP BY day", (start, end)):
        spend[r["day"]] = int(r["s"] or 0)
    todos = {}
    for r in conn.execute("SELECT day, done, COUNT(*) AS n FROM todos WHERE day>=? AND day<=? GROUP BY day, done", (start, end)):
        todos.setdefault(r["day"], {"done": 0, "open": 0})["done" if r["done"] else "open"] = int(r["n"])
    days = []
    tot = {"spend": 0, "nights": 0, "nights_done": 0, "workouts": 0, "todos_done": 0}
    for i in range(ndays):
        date = _date_add(start, i)
        nr = nights.get(date)
        night = None
        if nr is not None:
            k = common.NIGHT_KIND.get(nr["kind"]) or {}
            night = {"kind": nr["kind"], "kind_name": k.get("name", nr["kind"]), "ask": bool(k.get("ask")),
                     "done": int(nr["done"] or 0), "checked": nr["checked_ts"] is not None}
            tot["nights"] += 1
            if night["done"]:
                tot["nights_done"] += 1
                if nr["kind"] == "workout":
                    tot["workouts"] += 1
        td = todos.get(date, {"done": 0, "open": 0})
        days.append({"date": date, "night": night, "spend": spend.get(date, 0),
                     "todos_done": td["done"], "todos_open": td["open"]})
        tot["spend"] += spend.get(date, 0)
        tot["todos_done"] += td["done"]
    if my_events is not None:                      # §28 주·월에 내 일정 표시
        ev = my_events.in_range(conn, start, end, common.today_str(CFG))
        for d in days:
            d["events"] = ev.get(d["date"], [])
    b = api_budget_get(conn, {"date": [start]}, None)
    return {"start": start, "end": end, "days": days, "totals": tot,
            "budget": {"month": b["month"], "plan": b["plan"], "total": b["total"]}}


def _note_path(name):
    if name == "pinned":
        fn = PINNED_NOTE_FILE
    elif _valid_date(name):
        fn = name + ".md"
    else:
        raise ApiError(400, "name 은 YYYY-MM-DD 또는 'pinned' 여야 합니다")
    return os.path.join(common.NOTES_DIR, fn)


def _note_ver(text):
    """§27 메모의 판 번호 = 내용 해시. 시각(mtime)이 아니라 내용이라 같은 글이면 같은 판이다."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def api_note_get(conn, query, body):
    name = (query.get("name") or [""])[0].strip()
    path = _note_path(name)
    text = ""
    with _notes_lock:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as f:
                text = f.read()
    return {"name": name, "text": text, "ver": _note_ver(text)}


def api_note_save(conn, query, body):
    name = body.get("name")
    if not isinstance(name, str):
        raise ApiError(400, "name 이 필요합니다")
    path = _note_path(name.strip())
    text = body.get("text")
    text = "" if text is None else str(text)
    base = body.get("base")
    with _notes_lock:
        os.makedirs(common.NOTES_DIR, exist_ok=True)
        cur = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as f:
                cur = f.read()
        # §27 두 기기에서 쓴다: 보낸 쪽이 불러온 판(base)과 지금 판이 다르면 그 사이 다른 기기가 바꾼 것 — 덮어쓰지 않는다.
        # base 를 안 보내는 옛 방식은 그대로 저장한다.
        if isinstance(base, str) and base and _note_ver(cur) != base and cur != text:
            if body.get("on_conflict") == "append":
                # 창을 닫는 중이라 되물을 수 없을 때: 아무것도 버리지 않고 뒤에 붙인다
                tail = body.get("tail")
                if isinstance(tail, str):          # 보낸 쪽이 '덧붙인 부분만' 알려 준 경우
                    text = cur + tail
                else:
                    sep = "" if (not cur or cur.endswith("\n")) else "\n"
                    text = cur + sep + "\n— 다른 기기에서 쓴 것 —\n" + text
            else:
                return {"ok": False, "conflict": True, "text": cur, "ver": _note_ver(cur)}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    return {"ok": True, "ver": _note_ver(text)}


def api_pause(conn, query, body):
    now = common.now_ts()
    if "until" in body:
        if body.get("until") != "today":
            raise ApiError(400, "until 은 'today' 만 지원합니다")
        until = common.day_window(common.today_str(CFG, now), CFG)[1]
    elif "minutes" in body:
        minutes = _int(body, "minutes", minimum=0)
        until = None if minutes == 0 else now + minutes * 60.0
    else:
        raise ApiError(400, "minutes 또는 until 이 필요합니다")
    common.set_state(conn, "paused_until", None if until is None else repr(float(until)))
    if until is None:
        log.info("일시정지 해제")
    else:
        log.info("일시정지 → %s 까지", common.fmt_hm(until))
    return {"paused_until": until}


# ------------------------------------------------------------------ Paperdesk 읽기 기록 (프록시)
PAPERDESK_URL = "http://127.0.0.1:8770"


def _paper_short(fname):
    """'2018_FME_Antwi_A review....pdf' -> 'Antwi 2018' (Paperdesk 파일명 규칙: 연도_저널_저자_제목)"""
    parts = os.path.splitext(fname)[0].split("_", 3)
    if len(parts) >= 3 and parts[0].isdigit():
        return parts[2] + " " + parts[0]
    return os.path.splitext(fname)[0][:30]


def api_reading(conn, query, body):
    """GET /api/reading?date=YYYY-MM-DD            — Athenaeum(논문 앱)에서 그 날 읽은 논문과 시간
       GET /api/reading?start=D1&end=D2            — 기간(둘 다 포함, 최대 62일) 합계: 논문별 시간 합산, 읽은 날 수
    Athenaeum 서버가 꺼져 있으면 {"available": false}. UI 는 static/reading.js 가 담당."""
    import urllib.request
    q_start = (query.get("start") or [""])[0].strip()
    q_end = (query.get("end") or [""])[0].strip()
    date = (query.get("date") or [""])[0].strip() or common.today_str(CFG, common.now_ts())
    if q_start or q_end:
        _require_date(q_start, "start"); _require_date(q_end, "end")
        if q_end < q_start:
            raise ApiError(400, "end 가 start 보다 앞섭니다")
        d0 = datetime.datetime.strptime(q_start, "%Y-%m-%d"); d1 = datetime.datetime.strptime(q_end, "%Y-%m-%d")
        if (d1 - d0).days > 62:
            raise ApiError(400, "기간은 최대 62일")
    else:
        _require_date(date)
    try:
        with urllib.request.urlopen(PAPERDESK_URL + "/api/reading_stats?days=400", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"available": False, "date": date, "start": q_start or date, "end": q_end or date}
    days = data.get("days") or {}
    if not (q_start or q_end):
        day = days.get(date) or {"sec": 0, "papers": []}
        papers = [{"file": p.get("file", ""), "title": p.get("title", ""), "short": _paper_short(p.get("file", "")), "sec": p.get("sec", 0)}
                  for p in day.get("papers", [])]
        return {"available": True, "date": date, "sec": day.get("sec", 0), "papers": papers}
    agg, total, days_read = {}, 0, 0
    for dkey in sorted(days.keys()):
        if dkey < q_start or dkey > q_end:
            continue
        day = days[dkey] or {}
        if day.get("sec", 0) > 0:
            days_read += 1
        total += day.get("sec", 0)
        for p in day.get("papers", []):
            f = p.get("file", "")
            a = agg.setdefault(f, {"file": f, "title": p.get("title", ""), "short": _paper_short(f), "sec": 0, "days": 0})
            a["sec"] += p.get("sec", 0); a["days"] += 1
    papers = sorted(agg.values(), key=lambda a: -a["sec"])
    return {"available": True, "start": q_start, "end": q_end, "sec": total, "days_read": days_read, "papers": papers}


# ------------------------------------------------------------------ 학교 공지 (DESIGN §16.6)
NOTICE_NEW_DAYS = 7          # 게시판의 NEW 배지 대신: 올라온 지 이 일수 안이면 is_new
NOTICE_LIMIT_MAX = 200


def _notices_cfg():
    """notices.json 은 요청 때마다 다시 읽는다(손편집 즉시 반영). 읽기도 락 안에서 — Windows 는
    열려 있는 파일을 os.replace 하지 못하므로 버전 갱신(복사)과 읽기가 겹치면 실패한다."""
    with _notices_cfg_lock:
        return common.load_notices_config()


def _need_notices():
    if notices is None:
        raise ApiError(500, "공지 모듈을 불러오지 못했습니다: %s" % NOTICES_ERROR)
    return notices


def _notices_sync_now(conn, cfg, now):
    """동기 수집. 예외는 notices.sync 가 삼키고 errors 로 돌려준다."""
    return notices.sync(conn, cfg, now)


def _notices_sync_bg(cfg):
    """백그라운드 수집 시작. 이미 수집 중이면 아무것도 하지 않고 False."""
    if notices is None:
        return False
    if not _notices_busy.acquire(False):
        return False

    def worker():
        try:
            c = common.connect(CFG)
            try:
                res = notices.sync(c, cfg, common.now_ts())
                log.info("공지 수집: 새 %d, 갱신 %d, 오류 %d", res["new"], res["updated"], len(res["errors"]))
            finally:
                c.close()
        except Exception:
            log.exception("공지 백그라운드 수집 실패")
        finally:
            _notices_busy.release()

    threading.Thread(target=worker, name="notices-sync", daemon=True).start()
    return True


def _notices_today():
    return common.today_str(CFG, common.now_ts())


def _d_day(deadline, today):
    """마감까지 남은 일수. 마감이 없거나 형식이 이상하면 None."""
    if not deadline:
        return None
    try:
        d = datetime.datetime.strptime(str(deadline)[:10], "%Y-%m-%d").date()
        t = datetime.datetime.strptime(today, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (d - t).days


def _row_keys(row):
    try:
        return set(row.keys())
    except AttributeError:
        return set(row)


def _notice_item(row, board_names, today):
    d_day = _d_day(row["deadline"], today)
    posted = row["posted"] or ""
    is_new = False
    if posted:
        age = _d_day(posted, today)
        is_new = age is not None and -NOTICE_NEW_DAYS <= age <= 0
    # §17: 분석이 끝난 글은 카드에서 제목 옆에 판정 점을 찍는다(LEFT JOIN 으로 딸려온 analysis).
    verdict = None
    if "analysis" in _row_keys(row) and row["analysis"]:
        try:
            got = notices.normalize_analysis(json.loads(row["analysis"])) if notices else None
        except (ValueError, TypeError):
            got = None
        if got:
            verdict = got["verdict"]
    return {
        "id": row["id"],
        "board": row["board"],
        "board_name": board_names.get(row["board"], row["board"]),
        "title": row["title"] or "",
        "url": row["url"] or "",
        "posted": posted,
        "cate": row["cate"] or "",
        "tier": row["tier"] or "other",
        "tag": row["tag"] or "",
        "deadline": row["deadline"] or None,
        "d_day": d_day,
        "read": row["read_ts"] is not None,
        "is_new": is_new,
        "has_analysis": verdict is not None,
        "verdict": verdict,
        "analyzing": _notice_is_analyzing(row["id"]),
    }


def _notice_sort_key(it):
    """미열람 우선 → 마감 임박(d_day ≥ 0 오름차순) → 마감 없음 → 지난 마감 → posted 내림차순."""
    d = it["d_day"]
    if d is None:
        grp, dd = 1, 0
    elif d >= 0:
        grp, dd = 0, d
    else:
        grp, dd = 2, -d
    return (0 if not it["read"] else 1, grp, dd, _neg_date(it["posted"]), -it["id"])


def _neg_date(posted):
    """'YYYY-MM-DD' 를 내림차순 정렬용 음수로. 비어 있으면 맨 뒤."""
    try:
        return -int(posted.replace("-", ""))
    except (ValueError, AttributeError):
        return 0


def api_notices(conn, query, body):
    """GET /api/notices?tier=top|all&limit=30 — 카드에 뿌릴 공지 목록.
    수집 주기가 지났으면 백그라운드로 수집을 걸고, 응답은 지금 DB 에 있는 것으로 바로 돌려준다."""
    _need_notices()
    tier = (query.get("tier") or ["top"])[0].strip() or "top"
    if tier not in ("top", "all"):
        raise ApiError(400, "tier 는 top 또는 all 이어야 합니다")
    try:
        limit = int((query.get("limit") or ["30"])[0])
    except ValueError:
        raise ApiError(400, "limit 은 정수여야 합니다")
    limit = max(1, min(NOTICE_LIMIT_MAX, limit))

    cfg = _notices_cfg()
    now = common.now_ts()
    last_fetch = 0.0
    try:
        last_fetch = float(common.get_state(conn, "notices_last_fetch") or 0)
    except (TypeError, ValueError):
        last_fetch = 0.0
    interval = float(cfg.get("fetch_interval_hours", 6) or 0) * 3600.0
    if cfg.get("boards") and (interval <= 0 or now - last_fetch > interval):
        _notices_sync_bg(cfg)

    board_names = dict((b["id"], b["name"]) for b in cfg.get("boards") or [])
    today = _notices_today()
    rows = conn.execute(
        "SELECT n.*, d.analysis AS analysis FROM notices n "
        "LEFT JOIN notice_detail d ON d.notice_id = n.id "
        "WHERE n.tier IS NULL OR n.tier != 'hide'").fetchall()
    items = [_notice_item(r, board_names, today) for r in rows]
    counts = {
        "top": sum(1 for it in items if it["tier"] == "top"),
        "info": sum(1 for it in items if it["tier"] == "info"),
        "other": sum(1 for it in items if it["tier"] not in ("top", "info")),
        "unread": sum(1 for it in items if not it["read"]),
    }
    counts["unread_top"] = sum(1 for it in items if it["tier"] == "top" and not it["read"])
    if tier == "top":
        items = [it for it in items if it["tier"] == "top"]
    items.sort(key=_notice_sort_key)
    err = common.get_state(conn, "notices_last_error") or None
    return {
        "items": items[:limit],
        "tier": tier,
        "total": len(items),
        "last_fetch": last_fetch or None,
        "fetching": _notices_busy.locked(),
        "error": err,
        "counts": counts,
    }


def api_notices_read(conn, query, body):
    """POST /api/notices/read — {"id": n} 하나 또는 {"all": true} 전부 열람 처리."""
    _need_notices()
    now = common.now_ts()
    if body.get("all"):
        conn.execute("UPDATE notices SET read_ts=? WHERE read_ts IS NULL", (now,))
        conn.commit()
        return {"ok": True, "read_ts": now}
    nid = _int(body, "id")
    cur = conn.execute("UPDATE notices SET read_ts=? WHERE id=? AND read_ts IS NULL", (now, nid))
    conn.commit()
    if cur.rowcount == 0:
        row = conn.execute("SELECT id FROM notices WHERE id=?", (nid,)).fetchone()
        if row is None:
            raise ApiError(404, "그런 공지가 없습니다: %d" % nid)
    return {"ok": True, "read_ts": now}


def api_notices_refresh(conn, query, body):
    """POST /api/notices/refresh — 지금 바로 수집(동기). 실패해도 200 + errors 로 돌려준다."""
    _need_notices()
    cfg = _notices_cfg()
    wait = float(cfg.get("timeout_sec", 15) or 15) * max(1, len(cfg.get("boards") or [])) + 5.0
    if not _notices_busy.acquire(True, wait):
        raise ApiError(503, "이미 공지를 수집하는 중입니다. 잠시 후 다시 시도하세요")
    try:
        res = _notices_sync_now(conn, cfg, common.now_ts())
    finally:
        _notices_busy.release()
    return {"new": res["new"], "updated": res["updated"], "errors": res["errors"]}


# ------------------------------------------------------------------ §17 본문 분석
def _notice_is_analyzing(nid):
    with _notices_analyzing_lock:
        return int(nid) in _notices_analyzing


def _notice_row(conn, nid):
    row = conn.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
    if row is None:
        raise ApiError(404, "그런 공지가 없습니다: %d" % nid)
    return row


def _notice_detail_payload(conn, row, detail):
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "url": row["url"] or "",
        "body": (detail or {}).get("body") or None,
        "files": (detail or {}).get("files") or [],
        "analysis": (detail or {}).get("analysis"),
        "analyzed_ts": (detail or {}).get("analyzed_ts"),
        "model": (detail or {}).get("model") or None,
        "available": notices.analysis_available(),
        "analyzing": _notice_is_analyzing(row["id"]),
    }


def api_notices_detail(conn, query, body):
    """GET /api/notices/detail?id=N — 본문·첨부·분석 결과.
    본문이 아직 없으면 **그 자리에서 한 번** 받아 저장한다(동기, timeout_sec). 실패해도 200 —
    body 가 null 로 나가고 카드는 '원문 열기' 만 보여 준다. 같은 글은 두 번 받지 않는다."""
    _need_notices()
    try:
        nid = int((query.get("id") or [""])[0])
    except (TypeError, ValueError):
        raise ApiError(400, "id 는 정수여야 합니다")
    row = _notice_row(conn, nid)
    cfg = _notices_cfg()
    boards = dict((b["id"], b["list_url"]) for b in cfg.get("boards") or [])
    detail = notices.load_detail(conn, nid)
    if not (detail and detail["body"]):
        try:
            detail = notices.ensure_detail(conn, row, cfg, boards=boards)
        except Exception as e:
            log.info("공지 본문 수집 실패 (id=%s): %s", nid, e)
            detail = notices.load_detail(conn, nid)
    return _notice_detail_payload(conn, row, detail)


def _notices_analyze_bg(nid, cfg):
    """백그라운드 분석 1건. 슬롯(BoundedSemaphore)을 기다렸다가 claude 를 부른다."""
    def worker():
        try:
            _notices_analyze_slots.acquire()
            try:
                c = common.connect(CFG)
                try:
                    got = notices.analyze_notice(c, nid, cfg)
                    log.info("공지 분석 %s: %s", nid, (got or {}).get("verdict") or "실패")
                finally:
                    c.close()
            finally:
                _notices_analyze_slots.release()
        except Exception:
            log.exception("공지 분석 실패 (id=%s)", nid)
        finally:
            with _notices_analyzing_lock:
                _notices_analyzing.discard(nid)

    threading.Thread(target=worker, name="notices-analyze-%d" % nid, daemon=True).start()


def api_notices_analyze(conn, query, body):
    """POST /api/notices/analyze {"id": N} — 본문이 없으면 먼저 받고 claude 로 분석.
    즉시 {"started": true} 로 돌아가고, 진행 상태는 /api/notices/detail 의 analyzing 으로 본다."""
    _need_notices()
    nid = _int(body, "id")
    row = _notice_row(conn, nid)
    if not notices.analysis_available():
        # claude CLI 가 없다 — 조용히 비활성(§17). 오류가 아니라 available:false 로 알린다.
        return {"started": False, "available": False, "analyzing": False, "id": row["id"]}
    with _notices_analyzing_lock:
        if nid in _notices_analyzing:
            return {"started": False, "available": True, "analyzing": True, "id": nid}
        _notices_analyzing.add(nid)
    try:
        _notices_analyze_bg(nid, _notices_cfg())
    except Exception:
        with _notices_analyzing_lock:
            _notices_analyzing.discard(nid)
        raise
    return {"started": True, "available": True, "analyzing": True, "id": nid}


# ------------------------------------------------------------------ 서랍 (§35): 사업자번호·주소·사인을 눌러서 꺼내 쓴다
class Raw(object):
    """핸들러가 JSON 대신 바이트를 보내고 싶을 때 돌려주는 값. _handle 이 _send_bytes 로 보낸다 (§35.2)."""

    def __init__(self, data, ctype, headers=None):
        self.data = data
        self.ctype = ctype
        self.headers = headers or {}


DRAWER_DIR = None                 # 테스트가 임시 폴더로 바꾼다. None 이면 data_dir(CFG)\drawer
DRAWER_MAX = 8 * 1024 * 1024      # 파일 상한
DRAWER_TEXT_MAX = 4000
DRAWER_LABEL_MAX = 100
_DRAWER_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
}


def _drawer_dir():
    d = DRAWER_DIR or os.path.join(common.data_dir(CFG), "drawer")
    os.makedirs(d, exist_ok=True)
    return d


def _drawer_safe_name(name):
    """경로 탈출 방지: basename 만, 허용 글자만, 줄기 50자 + 확장자 10자."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(u"[^0-9A-Za-z가-힣._ -]+", "_", base).strip(" ._")
    stem, ext = os.path.splitext(base or "file")
    return (stem[:50] or "file") + ext.lower()[:10]


def _drawer_row(r):
    kind = r["kind"]
    mime = r["mime"] or ""
    return {
        "id": r["id"], "kind": kind, "label": r["label"], "value": r["value"],
        "mime": mime or None, "size": r["size"],
        "url": ("/api/drawer/file?id=%d" % r["id"]) if kind == "file" else None,
        "is_image": bool(kind == "file" and mime.startswith("image/")),
    }


def _drawer_find(conn, did):
    row = conn.execute("SELECT * FROM drawer WHERE id=?", (did,)).fetchone()
    if row is None:
        raise ApiError(404, "없는 서랍 항목 id=%d" % did)
    return row


def _drawer_path(row):
    if row["kind"] != "file" or not row["file"]:
        return None
    return os.path.join(_drawer_dir(), os.path.basename(row["file"]))


def _drawer_label(body):
    label = body.get("label")
    label = "" if label is None else str(label).strip()
    if len(label) > DRAWER_LABEL_MAX:
        raise ApiError(400, "이름이 너무 깁니다 (%d자)" % DRAWER_LABEL_MAX)
    return label


def _drawer_value(body):
    value = body.get("value")
    value = "" if value is None else str(value).strip()
    if not value:
        raise ApiError(400, "내용이 비어 있습니다")
    if len(value) > DRAWER_TEXT_MAX:
        raise ApiError(400, "내용이 너무 깁니다 (%d자)" % DRAWER_TEXT_MAX)
    return value


def api_drawer_get(conn, query, body):
    rows = conn.execute("SELECT * FROM drawer ORDER BY sort_order, id").fetchall()
    return {"items": [_drawer_row(r) for r in rows]}


def api_drawer_add(conn, query, body):
    label = _drawer_label(body)
    data = body.get("data")
    if data is not None:
        # 파일: {label, name, data(base64)} — 행을 먼저 넣어 id 를 받고 그 id 로 파일 이름을 만든다
        name = _drawer_safe_name(body.get("name"))
        try:
            raw = base64.b64decode(str(data), validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "data 가 base64 가 아닙니다")
        if not raw:
            raise ApiError(400, "빈 파일입니다")
        if len(raw) > DRAWER_MAX:
            raise ApiError(413, "파일이 너무 큽니다 (%dMB 까지)" % (DRAWER_MAX // (1024 * 1024)))
        ext = os.path.splitext(name)[1].lower()
        mime = _DRAWER_MIME.get(ext, "application/octet-stream")
        if not label:
            label = os.path.splitext(name)[0] or "파일"
        cur = conn.execute(
            "INSERT INTO drawer(kind, label, value, file, mime, size, sort_order, created_ts) "
            "VALUES('file',?,?,?,?,?,0,?)", (label, name, "", mime, len(raw), common.now_ts()))
        did = cur.lastrowid
        fname = "%d_%s" % (did, name)
        with open(os.path.join(_drawer_dir(), fname), "wb") as f:
            f.write(raw)
        conn.execute("UPDATE drawer SET file=? WHERE id=?", (fname, did))
        conn.commit()
        return {"id": did}
    value = _drawer_value(body)
    if not label:
        label = value[:20]
    cur = conn.execute(
        "INSERT INTO drawer(kind, label, value, file, mime, size, sort_order, created_ts) "
        "VALUES('text',?,?,NULL,NULL,NULL,0,?)", (label, value, common.now_ts()))
    conn.commit()
    return {"id": cur.lastrowid}


def api_drawer_update(conn, query, body):
    did = _int(body, "id")
    row = _drawer_find(conn, did)
    sets, params = [], []
    if "label" in body:
        label = _drawer_label(body)
        if not label:
            raise ApiError(400, "이름이 비어 있습니다")
        sets.append("label=?")
        params.append(label)
    if "value" in body:
        if row["kind"] != "text":
            raise ApiError(400, "파일 항목의 내용은 고칠 수 없습니다 — 지우고 다시 넣으세요")
        sets.append("value=?")
        params.append(_drawer_value(body))
    if not sets:
        raise ApiError(400, "고칠 것이 없습니다")
    params.append(did)
    conn.execute("UPDATE drawer SET %s WHERE id=?" % ", ".join(sets), params)
    conn.commit()
    return {"ok": True}


def api_drawer_delete(conn, query, body):
    did = _int(body, "id")
    row = _drawer_find(conn, did)
    p = _drawer_path(row)
    conn.execute("DELETE FROM drawer WHERE id=?", (did,))
    conn.commit()
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
    return {"ok": True}


def api_drawer_file(conn, query, body):
    try:
        did = int((query.get("id") or [""])[0])
    except ValueError:
        raise ApiError(400, "id 가 정수가 아닙니다")
    row = _drawer_find(conn, did)
    p = _drawer_path(row)
    if not p or not os.path.isfile(p):
        raise ApiError(404, "파일이 없습니다: %s" % (row["file"] or ""))
    with open(p, "rb") as f:
        data = f.read()
    fn = quote(row["value"] or os.path.basename(p))
    return Raw(data, row["mime"] or "application/octet-stream",
               {"Content-Disposition": "inline; filename*=UTF-8''" + fn})


def api_drawer_reveal(conn, query, body):
    """탐색기에서 그 파일을 선택해 연다 — 업로드 창에 끌어 넣을 때. Windows 전용."""
    did = _int(body, "id")
    row = _drawer_find(conn, did)
    p = _drawer_path(row)
    if not p or not os.path.isfile(p):
        raise ApiError(404, "파일이 없습니다")
    if os.name != "nt":
        raise ApiError(501, "Windows 에서만 됩니다")
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    except OSError as e:
        raise ApiError(500, "탐색기를 못 열었습니다: %s" % e)
    return {"ok": True}


# ------------------------------------------------------------------ 다른 기기에서 쓰기 (§36): 로그인 · 이 PC 밖 요청 가르기 · 테일넷
# Athenaeum(2026-11-06) 과 같은 구조. 서버는 127.0.0.1 에만 묶여 있고 다른 기기는 tailscale serve 가 넘겨 주는 요청으로 들어온다.
# 이 PC 안의 요청은 그대로 통과, 밖의 요청은 「다른 기기에서 열기」 가 켜져 있고 로그인한 기기만. 꺼져 있으면 모두 거절(닫힌 쪽이 기본).
SESSION_DAYS = 60
SESSION_MAX = 12
COOKIE = "hor_s"
PW_ITER = 200000
LOGIN_FAIL_DELAY = 0.6            # 테스트가 0 으로 바꾼다
DEVICES_PATH = None               # 테스트가 임시 파일로 바꾼다. None 이면 data_dir(CFG)\devices.json
LOCAL_ONLY_MSG = "이 기능은 Orbit 이 도는 PC 에서만 됩니다"
_TL = threading.local()           # 요청을 받는 스레드의 표시: remote = 이 PC 밖에서 온 요청
_DEV_LOCK = threading.Lock()
_DEV_CACHE = {"mt": -1, "cfg": {}, "bad_t": 0}
_LOGIN_FAILS = []                 # 로그인 실패 시각 — 10분에 8번이면 잠시 막는다
_SESS_SEEN = {}                   # 세션 → 마지막으로 쓴 시각 (메모리에만)
_FWD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "X-Real-IP",
                "Tailscale-User-Login", "Tailscale-User-Name", "CF-Connecting-IP", "CF-Ray")
_OPEN_PATHS = {"/login", "/api/login"}
_OPEN_STATIC = {"manifest.json", "favicon.svg", "horarium.ico", "icon-180.png", "icon-192.png", "icon-512.png",
                "logo-mark.svg", "logo-mark-clock.svg", "PretendardVariable.woff2"}      # 로그인 화면·홈 화면 아이콘의 재료
_LOCAL_ONLY_POST = {"/api/devices", "/api/restart", "/api/drawer/reveal"}


def _devices_path():
    return DEVICES_PATH or os.path.join(common.data_dir(CFG), "devices.json")


def _dev_read_strict(recover=False):
    """devices.json 을 엄격히 읽는다(없으면 {}). 못 읽은 것을 '비어 있다' 로 보고 그 위에 쓰면 비밀번호·기기 목록이 지워지므로
    끝내 못 읽으면 예외. recover: 글이 깨진 채면 옆에 남기고 {} 에서 다시 시작."""
    path = _devices_path()
    last = None
    for _i in range(8):
        try:
            if not os.path.isfile(path):
                return {}
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            d = json.loads(raw) if raw.strip() else {}
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError) as e:
            last = e
            time.sleep(0.05)
    if recover and isinstance(last, ValueError):
        try:
            os.replace(path, "%s.broken_%s" % (path, time.strftime("%Y%m%d_%H%M%S")))
            return {}
        except OSError:
            pass
    raise RuntimeError("기기 설정 파일을 읽지 못했습니다 (%s)" % last)


def _save_json_atomic(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    for _i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def remote_cfg():
    """devices.json 의 remote 갈래 (파일이 바뀌었을 때만 다시 읽는다). 못 읽으면 지난 값 — 처음부터 못 읽었으면 {} 라 밖에서는 안 열린다."""
    path = _devices_path()
    try:
        st = os.stat(path)
        mt = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        _DEV_CACHE.update(mt=None, cfg={})
        return {}
    if _DEV_CACHE["mt"] != mt and time.time() - _DEV_CACHE["bad_t"] > 3:
        try:
            _DEV_CACHE.update(mt=mt, cfg=_dev_read_strict(), bad_t=0)
        except Exception:
            _DEV_CACHE["bad_t"] = time.time()
    r = _DEV_CACHE["cfg"].get("remote")
    return r if isinstance(r, dict) else {}


def _remote_change(change):
    """remote 갈래만 고친다 — 잠금 안에서 엄격히 다시 읽고 통째로 바꿔치기. 못 읽으면 쓰지 않고 예외."""
    with _DEV_LOCK:
        d = _dev_read_strict(recover=True)
        r = dict(d["remote"]) if isinstance(d.get("remote"), dict) else {}
        change(r)
        d["remote"] = r
        _save_json_atomic(_devices_path(), d)
        _DEV_CACHE.update(mt=-1, bad_t=0)
        return r


def _pw_hash(pw, salt, it=PW_ITER):
    return hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"), bytes.fromhex(salt), int(it)).hex()


def devices_set_password(pw):
    pw = str(pw or "")
    if len(pw) < 8:
        return {"error": "비밀번호는 8자 이상이어야 합니다"}
    salt = secrets.token_hex(16)
    _remote_change(lambda r: r.update(salt=salt, hash=_pw_hash(pw, salt), iter=PW_ITER, sessions={}))   # 바꾸면 모든 기기가 풀린다
    _SESS_SEEN.clear()
    return {"ok": True}


def is_local_request(headers, peer):
    """이 PC 안에서 온 요청인가. 밖의 요청은 이 PC 의 중계(tailscale serve)를 거쳐 상대가 127.0.0.1 이어도
    Host 가 localhost 가 아니거나 넘겨 준 흔적이 있다. 하나라도 걸리면 밖으로 본다."""
    if peer not in ("127.0.0.1", "::1"):
        return False
    host = (headers.get("Host") or "").strip().lower()
    name = host[:host.find("]") + 1] if host.startswith("[") else host.split(":")[0]
    if name not in ("localhost", "127.0.0.1", "[::1]", ""):
        return False
    for k in _FWD_HEADERS:
        if headers.get(k):
            return False
    return True


def _cookie_token(headers):
    m = re.search(r"(?:^|;\s*)%s=([A-Za-z0-9_\-]{20,200})" % COOKIE, headers.get("Cookie") or "")
    return m.group(1) if m else ""


def _tok_key(tok):
    return hashlib.sha256(str(tok).encode("ascii", "ignore")).hexdigest()


def _session_ok(tok, now=None):
    if not tok:
        return False
    now = time.time() if now is None else now
    k = _tok_key(tok)
    s = (remote_cfg().get("sessions") or {}).get(k)
    if not isinstance(s, dict) or now - float(s.get("t") or 0) > SESSION_DAYS * 86400:
        return False
    _SESS_SEEN[k] = now
    return True


def devices_login(headers, peer, body):
    """(상태 코드, 답, Set-Cookie 값) — 비밀번호가 맞으면 이 기기의 세션을 만든다"""
    rc = remote_cfg()
    if not rc.get("on") or not rc.get("hash"):
        return 403, {"error": "다른 기기에서 여는 것이 꺼져 있습니다"}, None
    now = time.time()
    _LOGIN_FAILS[:] = [t for t in _LOGIN_FAILS if now - t < 600]
    if len(_LOGIN_FAILS) >= 8:
        return 429, {"error": "로그인 시도가 너무 많습니다 — 10분 뒤에 다시 해 주세요"}, None
    try:
        good = hmac.compare_digest(_pw_hash(str((body or {}).get("password") or ""), rc.get("salt") or "00", rc.get("iter") or PW_ITER),
                                   str(rc.get("hash")))
    except Exception:
        good = False
    if not good:
        _LOGIN_FAILS.append(now)
        if LOGIN_FAIL_DELAY:
            time.sleep(LOGIN_FAIL_DELAY)
        return 401, {"error": "비밀번호가 맞지 않습니다"}, None
    tok = secrets.token_urlsafe(32)
    k = _tok_key(tok)
    ua = (headers.get("User-Agent") or "")[:120]
    ip = (headers.get("X-Forwarded-For") or peer or "")[:60]

    def ch(r):
        ss = r.get("sessions") if isinstance(r.get("sessions"), dict) else {}
        ss = {a: b for a, b in ss.items() if isinstance(b, dict) and now - float(b.get("t") or 0) < SESSION_DAYS * 86400}
        ss[k] = {"t": now, "ua": ua, "ip": ip}
        for old in sorted(ss, key=lambda a: ss[a].get("t", 0))[:max(0, len(ss) - SESSION_MAX)]:
            ss.pop(old, None)
        r["sessions"] = ss
    _remote_change(ch)
    secure = "; Secure" if (headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
    return 200, {"ok": True}, "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (COOKIE, tok, SESSION_DAYS * 86400, secure)


def devices_logout(headers):
    tok = _cookie_token(headers)
    if tok:
        k = _tok_key(tok)
        _remote_change(lambda r: (r.get("sessions") or {}).pop(k, None))
        _SESS_SEEN.pop(k, None)


def _ua_short(ua):
    ua = str(ua or "")
    dev = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
           else "Windows" if "Windows" in ua else "Mac" if "Mac OS" in ua else "기기")
    br = "Edge" if "Edg/" in ua else "Chrome" if ("Chrome/" in ua or "CriOS/" in ua) else "Safari" if "Safari/" in ua else "Firefox" if "Firefox/" in ua else ""
    return (dev + (" · " + br if br else "")).strip()


def _json_resp(code, obj, extra=None):
    return (code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)


def _deny(code, msg, path):
    if path.startswith("/api/"):
        return _json_resp(code, {"error": msg})
    page = (u"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Orbit</title>"
            u"<body style=\"font-family:'Malgun Gothic','Segoe UI',system-ui,sans-serif;max-width:520px;margin:18vh auto;padding:0 20px;line-height:1.7;color:#f2f2ef;background:#07080a\">"
            u"<h3 style='letter-spacing:.12em;font-weight:400;display:flex;align-items:center;gap:9px'><img src='/static/logo-mark.svg' alt='' style='height:20px;filter:invert(1)'>ORBIT</h3><p>%s</p></body>") % html_escape(msg)
    return (code, page.encode("utf-8"), MIME[".html"], None)


def gate(headers, peer, method, raw_path):
    """문지기(§36.2). None = 통과, 아니면 (code, data, ctype, extra_headers) — 핸들러는 그대로 보낸다. 순수(설정 파일 읽기만)."""
    path = urlparse(raw_path).path
    if method == "POST":
        # 브라우저는 POST 에 Origin(그 화면의 주소)을 붙인다. 다른 사이트의 화면이 보내는 요청은 받지 않는다 — 안에서도, 밖에서도.
        origin = (headers.get("Origin") or "").strip().lower()
        if origin:
            mine = set((headers.get(k) or "").strip().lower() for k in ("Host", "X-Forwarded-Host")) - {""}
            if origin == "null" or urlparse(origin).netloc not in mine:
                return _json_resp(403, {"error": "다른 사이트에서 보낸 요청은 받지 않습니다"})
    if is_local_request(headers, peer):
        return None
    rc = remote_cfg()
    if not rc.get("on") or not rc.get("hash"):
        return _deny(403, "이 Orbit 은 다른 기기에서 여는 것이 꺼져 있습니다. Orbit 이 도는 PC 에서 기록 구역 → 다른 기기 → 켜기.", path)
    if path in _OPEN_PATHS or (path.startswith("/static/") and path[len("/static/"):] in _OPEN_STATIC):
        return None
    if _session_ok(_cookie_token(headers)):
        if method == "POST" and path in _LOCAL_ONLY_POST:
            return _json_resp(403, {"error": LOCAL_ONLY_MSG})
        return None
    if method == "POST" or path.startswith("/api/"):
        return _json_resp(401, {"error": "로그인이 필요합니다", "login": True})
    return (302, b"", "text/plain; charset=utf-8", {"Location": "/login?next=" + quote(raw_path, safe="")})


# ---- Tailscale: 설치·로그인은 사용자가, 여기서는 상태를 보고 serve(테일넷 → 127.0.0.1:PORT)만 건다. Funnel 은 쓰지 않는다 ----
_TS = {"t": 0, "v": None}
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW — pythonw 서버가 콘솔 창을 띄우지 않게


def _port():
    try:
        return int(CFG.get("port", 8771))
    except Exception:
        return 8771


def find_tailscale():
    for p in (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if os.path.isfile(p):
            return p
    return shutil.which("tailscale")


def _ts_run(exe, *args, **kw):
    timeout = kw.get("timeout", 25)
    try:
        r = subprocess.run([exe] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode, ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired as e:
        part = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace").strip()
        return 124, ("%d초 안에 끝나지 않았습니다. " % timeout) + part
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


def serve_urls(status_text, port):
    """`tailscale serve status` 글에서 이 포트로 열린 주소들. https 먼저. (100.x 주소는 serve 가 안 받으므로 없다)"""
    urls = []
    for line in (status_text or "").splitlines():
        tok = (line.strip().split() or [""])[0].rstrip("/")
        if tok.startswith(("http://", "https://")) and tok.endswith(":%d" % port) and tok not in urls:
            urls.append(tok)
    return sorted(urls, key=lambda u: (not u.startswith("https://"), len(u)))


def tailscale_view(fresh=False):
    now = time.time()
    if not fresh and _TS["v"] is not None and now - _TS["t"] < 20:
        return _TS["v"]
    port = _port()
    exe = find_tailscale()
    v = {"found": bool(exe), "running": False, "state": "", "name": "", "dns": "", "serving": False, "urls": []}
    if exe:
        rc, out = _ts_run(exe, "status", "--json", timeout=10)
        try:
            st = json.loads(out)
        except Exception:
            st = None
        if rc == 0 and isinstance(st, dict):
            v["state"] = str(st.get("BackendState") or "")
            v["running"] = v["state"] == "Running"
            me = st.get("Self") or {}
            v["dns"] = str(me.get("DNSName") or "").rstrip(".")
            v["name"] = v["dns"].split(".")[0] if v["dns"] else str(me.get("HostName") or "")
        if v["running"]:
            rc, out = _ts_run(exe, "serve", "status", timeout=10)
            v["urls"] = serve_urls(out, port)
            v["serving"] = bool(v["urls"]) and ("127.0.0.1:%d" % port) in out
    _TS.update(t=now, v=v)
    return v


def tailscale_serve(on=True):
    exe = find_tailscale()
    if not exe:
        return {"error": "이 PC 에 Tailscale 이 없습니다 — tailscale.com/download 에서 설치하고 로그인한 뒤 다시 눌러 주세요"}
    if not tailscale_view(True).get("running"):
        return {"error": "Tailscale 이 로그인(연결)되어 있지 않습니다 — 작업 표시줄의 Tailscale 아이콘에서 로그인한 뒤 다시 눌러 주세요"}
    port = _port()
    target = "http://127.0.0.1:%d" % port
    if on:
        # §36.3 테일넷 HTTPS 가 켜져 있으면 https(보안 컨텍스트 — 서랍의 그림 복사·홈 화면 앱). 안 되면 http 로 물러선다
        rc, out = _ts_run(exe, "serve", "--bg", "--https=%d" % port, target, timeout=40)
        if rc != 0:
            rc2, out2 = _ts_run(exe, "serve", "--bg", "--http=%d" % port, target, timeout=40)
            if rc2 == 0:
                rc, out = 0, out2 + "\n(테일넷 HTTPS 가 꺼져 있어 http 로 열었습니다: " + out[:200] + ")"
    else:
        rc1, o1 = _ts_run(exe, "serve", "--https=%d" % port, "off", timeout=40)
        rc2, o2 = _ts_run(exe, "serve", "--http=%d" % port, "off", timeout=40)
        rc, out = (0 if (rc1 == 0 or rc2 == 0) else rc1), (o1 + "\n" + o2).strip()
    _TS["v"] = None
    return {"ok": True, "out": out[:600]} if rc == 0 else {"error": "설정하지 못했습니다: " + out[:400]}


def devices_view(is_remote, fresh=False):
    rc = remote_cfg()
    out = {"on": bool(rc.get("on")), "has_password": bool(rc.get("hash")), "remote": bool(is_remote), "session_days": SESSION_DAYS}
    if not is_remote:      # 기기 목록·주소는 이 PC 에서만 보인다
        ss = rc.get("sessions") if isinstance(rc.get("sessions"), dict) else {}
        out["sessions"] = sorted(({"t": s.get("t"), "dev": _ua_short(s.get("ua")), "seen": _SESS_SEEN.get(k)}
                                  for k, s in ss.items() if isinstance(s, dict)), key=lambda x: -(x.get("t") or 0))
        out["tailscale"] = tailscale_view(fresh)
    return out


def api_devices_get(conn, query, body):
    return devices_view(getattr(_TL, "remote", False), "check" in query)


def api_devices_post(conn, query, body):
    if getattr(_TL, "remote", False):          # 문지기가 먼저 막지만 한 번 더
        raise ApiError(403, LOCAL_ONLY_MSG)
    op = body.get("op")
    if op == "password":
        r = devices_set_password(body.get("password"))
    elif op == "on":
        on = bool(body.get("on"))
        if on and not remote_cfg().get("hash"):
            r = {"error": "먼저 비밀번호를 정해 주세요"}
        else:
            _remote_change(lambda x: x.update(on=on))
            r = {"ok": True}
    elif op == "logout_all":
        _remote_change(lambda x: x.update(sessions={}))
        _SESS_SEEN.clear()
        r = {"ok": True}
    elif op == "serve":
        r = tailscale_serve(bool(body.get("on", True)))
    else:
        raise ApiError(400, "모르는 op: %r" % (op,))
    v = devices_view(False, op == "serve")
    v["result"] = r
    return v


ROUTES = {
    ("GET", "/api/day"): api_day,
    ("GET", "/api/notices"): api_notices,
    ("GET", "/api/notices/detail"): api_notices_detail,
    ("POST", "/api/notices/read"): api_notices_read,
    ("POST", "/api/notices/refresh"): api_notices_refresh,
    ("POST", "/api/notices/analyze"): api_notices_analyze,
    ("GET", "/api/reading"): api_reading,
    ("GET", "/api/range"): api_range,
    ("GET", "/api/status"): api_status,
    ("GET", "/api/config"): api_config,
    ("GET", "/api/rules"): api_rules,
    ("GET", "/api/names"): api_names,
    ("GET", "/api/todos"): api_todos_get,
    ("GET", "/api/night"): api_night_get,
    ("GET", "/api/note"): api_note_get,
    ("GET", "/api/budget"): api_budget_get,
    ("GET", "/api/period"): api_period_get,
    ("GET", "/api/schedule"): api_schedule_get,
    ("GET", "/api/events"): api_events_get,
    ("GET", "/api/anki"): api_anki_get,
    ("POST", "/api/events"): api_events_add,
    ("POST", "/api/events/update"): api_events_update,
    ("POST", "/api/events/delete"): api_events_delete,
    ("POST", "/api/events/retry"): api_events_retry,
    ("POST", "/api/override"): api_override,
    ("POST", "/api/override/delete"): api_override_delete,
    ("POST", "/api/rule"): api_rule,
    ("POST", "/api/rule/delete"): api_rule_delete,
    ("POST", "/api/todos"): api_todos_add,
    ("POST", "/api/todos/update"): api_todos_update,
    ("POST", "/api/todos/delete"): api_todos_delete,
    ("POST", "/api/night"): api_night_set,
    ("POST", "/api/night/confirm"): api_night_confirm,
    ("POST", "/api/night/delete"): api_night_delete,
    ("POST", "/api/note"): api_note_save,
    ("POST", "/api/budget/plan"): api_budget_plan_set,
    ("POST", "/api/spend"): api_spend_add,
    ("POST", "/api/spend/update"): api_spend_update,
    ("POST", "/api/spend/delete"): api_spend_delete,
    ("POST", "/api/pause"): api_pause,
    ("GET", "/api/drawer"): api_drawer_get,
    ("GET", "/api/drawer/file"): api_drawer_file,
    ("POST", "/api/drawer"): api_drawer_add,
    ("POST", "/api/drawer/update"): api_drawer_update,
    ("POST", "/api/drawer/delete"): api_drawer_delete,
    ("POST", "/api/drawer/reveal"): api_drawer_reveal,
    ("GET", "/api/devices"): api_devices_get,
    ("POST", "/api/devices"): api_devices_post,
    ("POST", "/api/restart"): api_restart,
}
_KNOWN_PATHS = set(p for (_m, p) in ROUTES.keys())


# ------------------------------------------------------------------ HTTP 핸들러
class Handler(BaseHTTPRequestHandler):
    server_version = "MAENG_worklog/1.0"
    sys_version = ""
    # protocol_version 기본(HTTP/1.0) → keep-alive 없음

    # --- 로깅: 콘솔 출력 금지, 로거로만
    def log_message(self, fmt, *args):
        level = logging.INFO if self.command == "POST" else logging.DEBUG
        try:
            log.log(level, "%s %s", self.address_string(), fmt % args)
        except Exception:
            pass

    def log_error(self, fmt, *args):
        try:
            log.warning("%s %s", self.address_string(), fmt % args)
        except Exception:
            pass

    # --- 응답 유틸
    def _send_bytes(self, code, data, ctype, extra=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send_json(self, code, obj):
        # allow_nan=False: 어떤 경로로든 Infinity/NaN 이 응답에 섞이면 브라우저 JSON.parse 가 깨지므로
        # 여기서 ValueError 로 막는다(핸들러의 except 가 500 으로 처리).
        data = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send_bytes(code, data, "application/json; charset=utf-8")

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "요청 본문이 올바른 JSON 이 아닙니다")
        if not isinstance(body, dict):
            raise ApiError(400, "요청 본문은 JSON 객체여야 합니다")
        return body

    # --- 정적 파일
    def _serve_index(self):
        path = os.path.join(common.STATIC_DIR, "index.html")
        if os.path.isfile(path):
            with open(path, "rb") as f:
                self._send_bytes(200, f.read(), MIME[".html"])
        else:
            self._send_bytes(200, FALLBACK_INDEX.encode("utf-8"), MIME[".html"])

    def _serve_static(self, name):
        name = os.path.basename(name.replace("\\", "/"))  # 디렉터리 탈출 방지
        if not name or name.startswith("."):
            raise ApiError(404, "not found")
        path = os.path.join(common.STATIC_DIR, name)
        if not os.path.isfile(path):
            raise ApiError(404, "not found: static/%s" % name)
        ext = os.path.splitext(name)[1].lower()
        with open(path, "rb") as f:
            self._send_bytes(200, f.read(), MIME.get(ext, "application/octet-stream"))

    # --- 라우팅
    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _serve_login(self):
        """§36.4 로그인 화면. 이 PC 에서는 로그인이 필요 없으므로 처음 화면으로 보낸다."""
        if not getattr(_TL, "remote", False):
            return self._send_bytes(302, b"", "text/plain; charset=utf-8", {"Location": "/"})
        with open(os.path.join(common.STATIC_DIR, "login.html"), "rb") as f:
            self._send_bytes(200, f.read(), MIME[".html"])

    def _login_post(self, path, peer):
        body = self._read_body()
        if path == "/api/login":
            try:
                code, out, cookie = devices_login(self.headers, peer, body)
            except Exception as e:
                code, out, cookie = 503, {"error": "지금은 로그인하지 못했습니다 — 잠시 뒤 다시 해 주세요 (%s)" % str(e)[:80]}, None
        else:
            try:
                devices_logout(self.headers)
            except Exception:
                pass
            code, out, cookie = 200, {"ok": True}, "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" % COOKIE
        c, data, ctype, _x = _json_resp(code, out)
        self._send_bytes(c, data, ctype, {"Set-Cookie": cookie} if cookie else None)

    def _handle(self, method):
        try:
            url = urlparse(self.path)
            path = url.path
            peer = (self.client_address or ("",))[0]
            g = gate(self.headers, peer, method, self.path)        # §36 문지기: 이 PC 밖에서 온 요청은 로그인한 기기만
            if g is not None:
                code, data, ctype, extra = g
                return self._send_bytes(code, data, ctype, extra)
            _TL.remote = not is_local_request(self.headers, peer)
            if method == "POST" and path in ("/api/login", "/api/logout"):
                return self._login_post(path, peer)
            if method == "GET":
                if path == "/login":
                    return self._serve_login()
                if path in ("/", "/index.html"):
                    return self._serve_index()
                if path.startswith("/static/"):
                    return self._serve_static(path[len("/static/"):])
            fn = ROUTES.get((method, path))
            if fn is None:
                if path in _KNOWN_PATHS:
                    raise ApiError(405, "%s 는 이 경로에서 허용되지 않습니다" % method)
                raise ApiError(404, "없는 경로: %s" % path)
            query = parse_qs(url.query)
            body = self._read_body() if method == "POST" else {}
            conn = common.connect(CFG)
            try:
                result = fn(conn, query, body)
            finally:
                conn.close()
            if isinstance(result, Raw):            # §35 바이트 응답 (서랍 파일)
                self._send_bytes(200, result.data, result.ctype, result.headers)
            else:
                self._send_json(200, result)
            after = getattr(fn, "after_send", None)
            if after is not None:
                # 응답을 완전히 내보낸 뒤에만 부수 효과(재시작 등) 시작
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                after()
        except ApiError as e:
            if e.code >= 500:
                log.error("%s %s -> %d %s", method, self.path, e.code, e.msg)
            self._send_json(e.code, {"error": e.msg})
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            log.exception("%s %s 처리 중 예외", method, self.path)
            self._send_json(500, {"error": str(e) or type(e).__name__})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 에서 SO_REUSEADDR 를 켜면 이미 쓰는 포트에도 bind 가 성공해 버리므로 끈다.
    allow_reuse_address = False
    # §48 기본 대기열 5 — 폰(테일넷 HTTPS)은 앱으로 돌아올 때 요청 스무 개쯤을 한꺼번에 보낸다. 넘친 연결은 윈도우가
    # 끊고 테일스케일이 502 를 돌려줬다(실측: 동시에 20개 → 502 5개). Athenaeum 도 같은 이유로 64.
    request_queue_size = 128


# ------------------------------------------------------------------ 시작
def setup_logging(cfg, debug=False):
    logger = logging.getLogger("MAENG_worklog.server")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    try:
        path = os.path.join(common.data_dir(cfg), "server.log")
        h = RotatingFileHandler(path, maxBytes=1000000, backupCount=3, encoding="utf-8")
    except Exception:
        h = logging.NullHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)
    return logger


def _port_in_use(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


BIND_RETRY_SEC = 10.0
BIND_RETRY_INTERVAL = 0.1      # §48 재시작 때 아무도 안 받는 틈을 줄인다(0.5 → 0.1초)


def _is_in_use_error(e):
    return getattr(e, "winerror", None) == 10048 or e.errno in (10048, 98, 48)


def _bind_with_retry(host, port):
    """§11.2: 포트가 아직 이전 서버(재시작 중)에 잡혀 있으면 0.5초 간격으로 최대 10초 재시도.
    끝까지 안 되면 기존 '이미 실행 중' 로직(로그 1줄, exit 0)."""
    deadline = time.time() + BIND_RETRY_SEC
    attempt = 0
    while True:
        attempt += 1
        if not _port_in_use(host, port):
            try:
                return Server((host, port), Handler)
            except OSError as e:
                if not _is_in_use_error(e):
                    log.error("서버 바인드 실패 %s:%d — %s", host, port, e)
                    sys.exit(1)
        if time.time() >= deadline:
            break
        if attempt == 1:
            log.info("포트 %d 사용 중 — %.0f초 동안 %.1f초 간격으로 바인드 재시도", port,
                     BIND_RETRY_SEC, BIND_RETRY_INTERVAL)
        time.sleep(BIND_RETRY_INTERVAL)
    log.info("이미 실행 중 (포트 %d 사용 중, 재시도 %d회) — 이 인스턴스는 종료", port, attempt)
    sys.exit(0)


def main(argv=None):
    global CFG
    ap = argparse.ArgumentParser(description="MAENG_worklog UI 서버")
    ap.add_argument("--port", type=int, default=None, help="포트 (기본 config.json 의 port)")
    ap.add_argument("--host", default=None, help="바인드 주소 (기본 config.json 의 host)")
    ap.add_argument("--db", default=None, help="DB 경로 (환경변수 MAENG_WORKLOG_DB 대신)")
    ap.add_argument("--debug", action="store_true", help="모든 요청을 로그에 남김")
    args = ap.parse_args(argv)

    if args.db:
        os.environ["MAENG_WORKLOG_DB"] = args.db
    CFG = common.load_config()
    setup_logging(CFG, args.debug)
    host = args.host or CFG.get("host") or "127.0.0.1"
    port = int(args.port or CFG.get("port") or 8771)

    # DB 열어 스키마 보장 (여기서 실패하면 뜰 이유가 없다)
    try:
        conn = common.connect(CFG)
        try:
            ensure_indexes(conn)
        except Exception as e:
            # 인덱스는 성능용이라 못 만들어도(잠금 등) 서버는 뜬다
            log.warning("보조 인덱스 생성 실패(무시): %s", e)
        conn.close()
    except Exception as e:
        log.error("DB 열기 실패 %s: %s", common.db_path(CFG), e)
        sys.exit(1)

    srv = _bind_with_retry(host, port)
    global _SERVER
    _SERVER = srv

    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                         name="http", daemon=True)
    t.start()
    # §46 작업 표시줄 이름표: 브라우저 --app 창(Orbit, data\taskbar_apps.json 의 다른 앱)에 앱 자신의 ID·다시 실행 명령·아이콘
    if CFG.get("taskbar_identity", True):
        try:
            import taskbar_id
            taskbar_id.start(common.data_dir(CFG))
        except Exception as e:
            log.warning("작업 표시줄 이름표를 켜지 못함(무시): %s", e)
    log.info("서버 시작 http://%s:%d  db=%s  engine=%s", host, port,
             common.db_path(CFG), "OK" if engine is not None else ("없음 (%s)" % ENGINE_ERROR))
    try:
        while t.is_alive():
            t.join(1.0)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — 종료")
    finally:
        try:
            if t.is_alive():
                srv.shutdown()
            srv.server_close()
        except Exception:
            pass
        log.info("서버 종료")


if __name__ == "__main__":
    main()
