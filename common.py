# -*- coding: utf-8 -*-
"""
common.py — MAENG_worklog 공용 모듈 (Python 3.8, 표준 라이브러리만)

경로 · 설정 · DB 스키마 · 카테고리 정의 · 하루 경계 시간 유틸.
tracker.py / engine.py / server.py 가 모두 이 모듈을 import 한다.
"""
import os
import re
import json
import time
import shutil
import sqlite3
import datetime

APP_NAME = "MAENG_worklog"
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
RULES_PATH = os.path.join(ROOT, "rules.json")
RULES_DEFAULT_PATH = os.path.join(ROOT, "rules.default.json")
NAMES_PATH = os.path.join(ROOT, "names.json")
NAMES_DEFAULT_PATH = os.path.join(ROOT, "names.default.json")
NOTICES_PATH = os.path.join(ROOT, "notices.json")
NOTICES_DEFAULT_PATH = os.path.join(ROOT, "notices.default.json")
NOTES_DIR = os.path.join(ROOT, "notes")
STATIC_DIR = os.path.join(ROOT, "static")

# ------------------------------------------------------------------ 설정
DEFAULT_CONFIG = {
    "poll_sec": 2,
    "heartbeat_sec": 30,
    "idle_sec": 600,
    "idle_grace_sec": {
        "focus": 900,
        "experiment": 86400,
        "meeting": 0,
        "break": 0,
        "distraction": 1800,
        "neutral": 0,
        "unknown": 0,
    },
    "day_start_hour": 4,
    "night_start_hour": 20,        # §19 밤 구간 시작. day_start_hour < 이 값 <= day_start_hour+24
    "night_prompt_hour": 16,
    "night_band_end_hour": 23,     # §25 밤 일정 표시 끝(20~23시). 연구 측정 구간(§19, 20~04시)과는 별개 — 보이는 것만
    "budget_card": False,          # §21.8 예산 카드 표시. 사용자가 "나중에" 로 미룸(2026-09-23). 표·API·데이터는 그대로       # §22 이 시각부터 오늘 밤을 아직 안 정했으면 사이드바 맨 위에 고르기
    "anki_card": False,            # §31.6 Anki 카드 표시. 아이폰이라 자동 동기화가 안 돼 사용자가 내림(2026-10-25). 모듈·API 는 그대로
    "night_research_min_sec": 1800,  # §19 밤 구간 집중+실험+회의 합이 이 값 이상이면 '연구' 이행
    "workout_interval_days": 2,    # §19 마지막 운동일로부터 이 날수 이상이면 '오늘 운동 차례'
    "display_start_hour": 6,
    "display_end_hour": 24,
    "absorb_sec": 10,
    "dead_after_sec": 120,
    "host": "127.0.0.1",
    "port": 8771,
    "data_dir": "data",
    "backup_keep_days": 14,
    "mask_titles": {"KakaoTalk.exe": "[카카오톡]"},
    # 원격 접속 감지: RDP(SM_REMOTESESSION) + Jump Desktop Connect 이벤트 로그
    "remote_detect": {
        "rdp": True,
        "jump_eventlog": True,
        "jump_provider": "Jump Desktop Connect",
        "poll_sec": 5,
        "backfill_days": 7,
        "max_session_hours": 3,
    },
    # 코드 파일(tracker.py, common.py)이 바뀌면 수집기가 스스로 재시작하는지 검사하는 주기(초). 0 이면 끔
    "self_update_check_sec": 30,
}


def load_config():
    """DEFAULT_CONFIG 위에 config.json 을 덮어쓴다(dict 값은 1단계 깊이 병합)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
        except Exception:
            user = {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def data_dir(cfg=None):
    cfg = cfg or load_config()
    d = os.path.expandvars(cfg.get("data_dir") or DEFAULT_CONFIG["data_dir"])
    if not os.path.isabs(d):
        # 상대 경로면 앱 폴더 기준 (예: "data" → Documents\MAENG_worklog\data).
        # AppData\Local 은 일부 앱(MSIX 패키지)에서 가상화/우회되어 사본이 갈라질 수 있어 앱 폴더 안을 기본으로 쓴다.
        d = os.path.join(ROOT, d)
    os.makedirs(d, exist_ok=True)
    return d


def db_path(cfg=None):
    env = os.environ.get("MAENG_WORKLOG_DB")
    if env:
        os.makedirs(os.path.dirname(os.path.abspath(env)) or ".", exist_ok=True)
        return env
    return os.path.join(data_dir(cfg), "worklog.db")


# ------------------------------------------------------------------ 카테고리
CATEGORIES = [
    {"id": "focus",       "name": "집중",     "color": "#3B82F6", "kind": "active", "manual": True},
    {"id": "experiment",  "name": "실험",     "color": "#22C55E", "kind": "active", "manual": True},
    {"id": "meeting",     "name": "회의",     "color": "#14B8A6", "kind": "active", "manual": True},
    {"id": "break",       "name": "휴식",     "color": "#F59E0B", "kind": "active", "manual": True},
    {"id": "distraction", "name": "딴짓",     "color": "#EF4444", "kind": "active", "manual": True},
    {"id": "neutral",     "name": "중립",     "color": "#CBD5E1", "kind": "active", "manual": True},
    {"id": "unknown",     "name": "미분류",   "color": "#9CA3AF", "kind": "active", "manual": False},
    {"id": "away",        "name": "자리비움", "color": "#E5E7EB", "kind": "system", "manual": False},
    {"id": "absent",      "name": "부재",     "color": "#F8FAFC", "kind": "system", "manual": False},
    {"id": "remote",      "name": "원격",     "color": "#A855F7", "kind": "system", "manual": True},
    {"id": "paused",      "name": "일시정지", "color": "#F3F4F6", "kind": "system", "manual": False},
    {"id": "off",         "name": "꺼짐",     "color": "#FFFFFF", "kind": "system", "manual": False},
]
CAT = {c["id"]: c for c in CATEGORIES}
ACTIVE_CATEGORIES = set(c["id"] for c in CATEGORIES if c["kind"] == "active")
MANUAL_CATEGORIES = [c["id"] for c in CATEGORIES if c["manual"]]

# §19 밤 일정 종류. CATEGORIES 와 별개다 — 밤 일정은 타임라인 카테고리가 아니라 사용자가 정한 계획이다.
# ask=True 만 다음날 출근 시 이행을 묻는다. 연구는 묻지 않고 engine.night_research_sec 으로 측정하고,
# 쉼은 확인할 것이 없다("행이 없다"=기록 안 함 과 "쉬기로 정했다"를 구분하려고 둔다).
NIGHT_KINDS = [
    {"id": "appointment", "name": "약속",     "ask": True},
    {"id": "english",     "name": "영어공부", "ask": True},
    {"id": "workout",     "name": "운동",     "ask": True},
    {"id": "study",       "name": "공부",     "ask": True},   # §30 사용자 요청 — 영어 말고 전공·시험 공부
    {"id": "research",    "name": "연구",     "ask": False},
    {"id": "rest",        "name": "쉼",       "ask": False},
]
NIGHT_KIND = {k["id"]: k for k in NIGHT_KINDS}
NIGHT_ASK_KINDS = [k["id"] for k in NIGHT_KINDS if k["ask"]]


# ------------------------------------------------------------------ DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    exe TEXT,
    title TEXT,
    pid INTEGER,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    last_seen REAL NOT NULL,
    end_ts REAL,
    end_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts);
CREATE TABLE IF NOT EXISTS overrides(
    id INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    label TEXT NOT NULL,
    note TEXT,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_overrides_start ON overrides(start_ts);
CREATE TABLE IF NOT EXISTS todos(
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    day TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL,
    done_ts REAL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_todos_day ON todos(day);
CREATE TABLE IF NOT EXISTS todo_progress(
    todo_id INTEGER PRIMARY KEY,
    started_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_plan(
    id INTEGER PRIMARY KEY,
    month TEXT NOT NULL,
    category TEXT NOT NULL,
    amount INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_plan_month ON budget_plan(month);
CREATE TABLE IF NOT EXISTS spend(
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    amount INTEGER NOT NULL,
    category TEXT,
    note TEXT,
    created_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_day ON spend(day);
CREATE TABLE IF NOT EXISTS plans(
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    title TEXT,
    day TEXT,
    time TEXT,
    kind TEXT,
    repeat TEXT,
    source_url TEXT,
    confidence TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'parsing',
    done INTEGER NOT NULL DEFAULT 0,
    skip_until TEXT,
    created_ts REAL NOT NULL,
    updated_ts REAL,
    done_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_plans_day ON plans(day);
CREATE TABLE IF NOT EXISTS state(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS notices(
    id INTEGER PRIMARY KEY,
    board TEXT NOT NULL,
    article_no TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    posted TEXT,
    cate TEXT,
    tier TEXT,
    tag TEXT,
    deadline TEXT,
    first_seen REAL,
    read_ts REAL,
    UNIQUE(board, article_no)
);
CREATE INDEX IF NOT EXISTS idx_notices_tier ON notices(tier, posted);
CREATE TABLE IF NOT EXISTS notice_detail(
    notice_id INTEGER PRIMARY KEY,
    body TEXT,
    files TEXT,
    fetched_ts REAL,
    analysis TEXT,
    analyzed_ts REAL,
    model TEXT
);
CREATE TABLE IF NOT EXISTS night(
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    note TEXT,
    created_ts REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    checked_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_night_day ON night(day);
CREATE TABLE IF NOT EXISTS drawer(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    file TEXT,
    mime TEXT,
    size INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL
);
"""


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def connect(cfg=None, path=None):
    """WAL + busy_timeout + Row factory. 스키마는 항상 보장."""
    p = path or db_path(cfg)
    conn = sqlite3.connect(p, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init_schema(conn)
    return conn


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row is not None else default


def set_state(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        conn.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    conn.commit()


# ------------------------------------------------------------------ 규칙
def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_rules_raw():
    """rules.json 원본 순서 그대로(파일 없으면 default 복사)."""
    if not os.path.exists(RULES_PATH):
        if os.path.exists(RULES_DEFAULT_PATH):
            shutil.copyfile(RULES_DEFAULT_PATH, RULES_PATH)
        else:
            _atomic_write_json(RULES_PATH, {"rules": []})
    try:
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"rules": []}
    rules = data.get("rules", []) if isinstance(data, dict) else data
    out = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        r.setdefault("target", "title")
        r.setdefault("is_regex", False)
        r.setdefault("priority", 100)
        r.setdefault("idle_limit_sec", None)
        r.setdefault("enabled", True)
        r.setdefault("category", "unknown")
        if r["category"] not in CAT:
            r["category"] = "unknown"
        out.append(r)
    return out


def load_rules():
    """엔진용: enabled 만, priority 내림차순(같으면 파일 순서)."""
    rules = [r for r in load_rules_raw() if r.get("enabled", True)]
    rules.sort(key=lambda r: -int(r.get("priority", 100)))
    return rules


def save_rules(rules):
    _atomic_write_json(RULES_PATH, {"rules": list(rules)})


# ------------------------------------------------------------------ 표시 이름 (DESIGN §15)
EMPTY_NAMES = {"browsers": [], "strip": [], "title": [], "exe": {}}


def _read_json_dict(path):
    """path 의 JSON dict. 없거나 깨졌거나 dict 가 아니면 None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _title_rule_key(r):
    """title 규칙의 동일성 키: (pattern, exe 소문자). 같은 키가 names.json 에 있으면 default 쪽은 버린다(사용자 편집 우선)."""
    exe = r.get("exe")
    return (str(r.get("pattern")), str(exe).strip().lower() if isinstance(exe, str) and exe.strip() else None)


def merge_names(user, default):
    """names.json(user) 에 names.default.json(default) 에만 있는 항목을 이어 붙인 dict (§15.1).

    names.json 은 처음 한 번만 default 에서 복사되므로, 그 뒤 default 에 규칙이 추가되어도 기존 PC 에는
    전파되지 않는다. 그래서 읽을 때마다 default 에만 있는 것을 뒤에 덧붙인다 — 사용자 규칙이 항상 앞(우선):
    browsers/strip 은 없는 값을 뒤에 추가, title 은 (pattern, exe) 키가 없는 규칙을 뒤에 추가,
    exe 표는 없는 키만 추가. 사용자가 default 규칙을 지우거나 고쳐도 default 원본이 뒤에 다시 붙으므로
    default 규칙을 없애려면 names.json 에서 같은 pattern 을 다른 이름으로 덮어써야 한다(README §5-1).
    두 인자를 바꾸지 않고 새 dict 를 돌려준다."""
    user = user if isinstance(user, dict) else {}
    default = default if isinstance(default, dict) else {}
    out = dict(user)
    ub = [b for b in (user.get("browsers") or []) if isinstance(b, str)]
    seen = set(b.strip().lower() for b in ub)
    for b in default.get("browsers") or []:
        if isinstance(b, str) and b.strip().lower() not in seen:
            ub.append(b)
            seen.add(b.strip().lower())
    out["browsers"] = ub
    us = [s for s in (user.get("strip") or []) if isinstance(s, str)]
    seen = set(us)
    for s in default.get("strip") or []:
        if isinstance(s, str) and s not in seen:
            us.append(s)
            seen.add(s)
    out["strip"] = us
    ut = [r for r in (user.get("title") or []) if isinstance(r, dict)]
    seen = set(_title_rule_key(r) for r in ut)
    for r in default.get("title") or []:
        if isinstance(r, dict) and _title_rule_key(r) not in seen:
            ut.append(dict(r))
            seen.add(_title_rule_key(r))
    out["title"] = ut
    ue = dict((k, v) for k, v in (user.get("exe") or {}).items() if isinstance(k, str))
    seen = set(k.strip().lower() for k in ue)
    for k, v in (default.get("exe") or {}).items():
        if isinstance(k, str) and k.strip().lower() not in seen:
            ue[k] = v
            seen.add(k.strip().lower())
    out["exe"] = ue
    return out


def load_names_raw():
    """names.json 원본 dict + names.default.json 에만 있는 항목(merge_names). 파일 없으면 default 복사,
    둘 다 없으면 빈 규칙 파일 생성. names.json 이 깨진 JSON 이면 {} 를 돌려준다(규칙 전체가 꺼져 exe 이름만
    표시 — 파일이 깨졌다는 신호. default 로 대체하지 않는다)."""
    if not os.path.exists(NAMES_PATH):
        if os.path.exists(NAMES_DEFAULT_PATH):
            shutil.copyfile(NAMES_DEFAULT_PATH, NAMES_PATH)
        else:
            _atomic_write_json(NAMES_PATH, dict(EMPTY_NAMES))
    data = _read_json_dict(NAMES_PATH)
    if data is None:
        return {}
    default = _read_json_dict(NAMES_DEFAULT_PATH)
    if default is None:
        return data
    # default 의 version 이 올라가면(기본 규칙의 pattern 이 바뀐 경우) names.json 을 새로 고친다.
    # merge_names 는 '없는 규칙 추가' 만 하므로, 고쳐진 규칙은 옛 names.json 이 앞에서 계속 이겨 버린다.
    # 옛 파일은 names.json.bak 으로 남긴다(손편집분 복구용).
    try:
        if int(default.get("version", 0)) > int(data.get("version", 0)):
            shutil.copyfile(NAMES_PATH, NAMES_PATH + ".bak")
            shutil.copyfile(NAMES_DEFAULT_PATH, NAMES_PATH)
            return dict(default)
    except Exception:
        pass
    return merge_names(data, default)


def normalize_names(data):
    """names dict 정규화: browsers 소문자 목록, strip 문자열 목록, title 항목 pattern/name/is_regex(기본 False)/exe(선택),
    exe 표는 {소문자 exe: 이름}. 형식이 어긋난 항목은 버린다. engine.display_name 은 이 형태를 기대한다."""
    data = data if isinstance(data, dict) else {}
    browsers = []
    for b in data.get("browsers") or []:
        if isinstance(b, str) and b.strip():
            browsers.append(b.strip().lower())
    strip = [s for s in (data.get("strip") or []) if isinstance(s, str) and s]
    title = []
    for r in data.get("title") or []:
        if not isinstance(r, dict):
            continue
        pattern = r.get("pattern")
        name = r.get("name")
        if not isinstance(pattern, str) or not pattern or not isinstance(name, str) or not name:
            continue
        item = {"pattern": pattern, "name": name, "is_regex": bool(r.get("is_regex", False))}
        exe = r.get("exe")
        if isinstance(exe, str) and exe.strip():
            item["exe"] = exe.strip().lower()
        title.append(item)
    exe_map = {}
    for k, v in (data.get("exe") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v:
            exe_map[k.strip().lower()] = v
    return {"browsers": browsers, "strip": strip, "title": title, "exe": exe_map}


def load_names():
    """엔진용 표시 이름 규칙 (정규화된 dict). engine.build_day(..., names=load_names())."""
    return normalize_names(load_names_raw())


# ------------------------------------------------------------------ 학교 공지 설정 (DESIGN §16.3)
EMPTY_NOTICES = {
    "version": 0,
    "fetch_interval_hours": 6,
    "pages_per_board": 2,
    "keep_days": 180,
    "timeout_sec": 15,
    "sleep_sec": 0.3,
    "boards": [],
    "rules": [],
    "category_tier": {},
    "profile": {},
}
NOTICE_TIERS = ("top", "info", "hide")
# 프로필 한 항목의 길이 상한 — 프롬프트가 통째로 사용자 파일에 휘둘리지 않게 (§17)
NOTICE_PROFILE_MAX = 400
NOTICE_PROFILE_KEYS_MAX = 20


def _num_key(data, default, key, minimum=None, maximum=None, cast=int):
    try:
        v = cast(data.get(key, default[key]))
    except (TypeError, ValueError):
        v = default[key]
    if minimum is not None and v < minimum:
        v = minimum
    if maximum is not None and v > maximum:
        v = maximum
    return v


def normalize_notices_config(data):
    """notices dict 정규화: 숫자 키는 범위 제한, boards 는 id/name/list_url/enabled 를 갖춘 것만,
    rules 는 tier(top|info|hide)·pattern 이 있는 것만(is_regex 기본 False, tag 기본 ''),
    category_tier 는 {카테고리명: tier}. 형식이 어긋난 항목은 버린다. notices.py 는 이 형태를 기대한다."""
    data = data if isinstance(data, dict) else {}
    d = EMPTY_NOTICES
    out = {
        "version": _num_key(data, d, "version", minimum=0),
        "fetch_interval_hours": _num_key(data, d, "fetch_interval_hours", minimum=0, cast=float),
        "pages_per_board": _num_key(data, d, "pages_per_board", minimum=1, maximum=20),
        "keep_days": _num_key(data, d, "keep_days", minimum=1, maximum=3650),
        "timeout_sec": _num_key(data, d, "timeout_sec", minimum=1, maximum=120, cast=float),
        "sleep_sec": _num_key(data, d, "sleep_sec", minimum=0, maximum=10, cast=float),
    }
    boards = []
    seen = set()
    for b in data.get("boards") or []:
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        url = b.get("list_url")
        if not isinstance(bid, str) or not bid.strip() or not isinstance(url, str) or not url.strip():
            continue
        bid = bid.strip()
        if bid in seen:
            continue
        seen.add(bid)
        name = b.get("name")
        boards.append({
            "id": bid,
            "name": name.strip() if isinstance(name, str) and name.strip() else bid,
            "list_url": url.strip(),
            "enabled": bool(b.get("enabled", True)),
        })
    out["boards"] = boards
    rules = []
    for r in data.get("rules") or []:
        if not isinstance(r, dict):
            continue
        tier = r.get("tier")
        pattern = r.get("pattern")
        if tier not in NOTICE_TIERS or not isinstance(pattern, str) or not pattern:
            continue
        tag = r.get("tag")
        rules.append({
            "tier": tier,
            "pattern": pattern,
            "is_regex": bool(r.get("is_regex", False)),
            "tag": tag if isinstance(tag, str) else "",
        })
    out["rules"] = rules
    ct = {}
    for k, v in (data.get("category_tier") or {}).items():
        if isinstance(k, str) and k.strip() and v in NOTICE_TIERS:
            ct[k.strip()] = v
    out["category_tier"] = ct
    # profile (§17): 사용자가 손으로 고치는 {항목: 설명} — 문자열 값만, 길이·개수 제한.
    prof = {}
    src = data.get("profile")
    if isinstance(src, dict):
        for k, v in src.items():
            if len(prof) >= NOTICE_PROFILE_KEYS_MAX:
                break
            if not isinstance(k, str) or not k.strip():
                continue
            if isinstance(v, (list, tuple)):
                v = u", ".join(str(x) for x in v)
            elif not isinstance(v, str):
                if v is None or isinstance(v, dict):
                    continue
                v = str(v)
            v = re.sub(r"\s+", " ", v).strip()
            if not v:
                continue
            prof[k.strip()[:60]] = v[:NOTICE_PROFILE_MAX]
    out["profile"] = prof
    return out


def load_notices_config_raw():
    """notices.json 원본 dict (없으면 notices.default.json 복사, 둘 다 없으면 빈 설정 생성).

    §15.1 의 names.json 과 같은 version 갱신 규칙: notices.default.json 의 version 이 더 크면
    옛 notices.json 을 notices.json.bak 으로 남기고 default 로 덮어쓴다(규칙 pattern 이 고쳐진 경우
    옛 파일이 계속 이기는 것을 막기 위해). 그 밖에는 default 에만 있는 최상위 키를 채워 넣는다."""
    if not os.path.exists(NOTICES_PATH):
        if os.path.exists(NOTICES_DEFAULT_PATH):
            shutil.copyfile(NOTICES_DEFAULT_PATH, NOTICES_PATH)
        else:
            _atomic_write_json(NOTICES_PATH, dict(EMPTY_NOTICES))
    data = _read_json_dict(NOTICES_PATH)
    if data is None:
        # 깨진 JSON → default 로 대체하지 않고 기본값만 (수집은 boards 가 비어 아무 일도 하지 않음)
        return {}
    default = _read_json_dict(NOTICES_DEFAULT_PATH)
    if default is None:
        return data
    try:
        if int(default.get("version", 0)) > int(data.get("version", 0)):
            shutil.copyfile(NOTICES_PATH, NOTICES_PATH + ".bak")
            shutil.copyfile(NOTICES_DEFAULT_PATH, NOTICES_PATH)
            return dict(default)
    except Exception:
        pass
    merged = dict(default)
    merged.update(data)  # 사용자 파일이 이김, default 에만 있는 새 키는 채워진다
    return merged


def load_notices_config():
    """학교 공지 수집 설정 (정규화된 dict). notices.sync / server 가 요청마다 읽는다."""
    return normalize_notices_config(load_notices_config_raw())


# ------------------------------------------------------------------ 시간
def now_ts():
    return time.time()


def day_window(date_str, cfg=None):
    """'YYYY-MM-DD' 의 day_start_hour 부터 다음날 같은 시각까지 (epoch)."""
    cfg = cfg or load_config()
    h = int(cfg.get("day_start_hour", 4))
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    start = d.replace(hour=h, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def night_window(date_str, cfg=None):
    """§19 'YYYY-MM-DD' 의 밤 구간 (start, end) epoch. 끝은 day_window 의 끝(다음날 day_start_hour).

    day_window 와 같은 자리에 둔다 — 밤은 하루의 부분집합이고 둘 다 같은 day_start_hour 를 기준으로 한다.
    ws + (hn-h0)*3600 은 '경과 초'이므로 서머타임이 있는 지역에서는 벽시계와 어긋난다(한국은 무해).
    """
    cfg = cfg or load_config()
    ws, we = day_window(date_str, cfg)
    h0 = int(cfg.get("day_start_hour", 4))
    hn = int(cfg.get("night_start_hour", 20))
    if hn <= h0 or hn > h0 + 24:
        hn = int(DEFAULT_CONFIG["night_start_hour"])
    if hn <= h0 or hn > h0 + 24:   # day_start_hour 가 이상해 기본값마저 못 쓸 때
        hn = h0 + 16
    start = ws + (hn - h0) * 3600
    if start > we:
        start = we
    return start, we


def date_of_ts(ts, cfg=None):
    """epoch → day_start_hour 기준 'YYYY-MM-DD' (새벽 4시 전은 전날)."""
    cfg = cfg or load_config()
    h = int(cfg.get("day_start_hour", 4))
    dt = datetime.datetime.fromtimestamp(ts) - datetime.timedelta(hours=h)
    return dt.strftime("%Y-%m-%d")


def today_str(cfg=None, now=None):
    return date_of_ts(now if now is not None else now_ts(), cfg)


def fmt_hm(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


# ------------------------------------------------------------------ 창 제목 정리
_ZERO_WIDTH = re.compile(u"[​‌‍⁠﻿]")


def exe_base(path):
    if not path:
        return ""
    return os.path.basename(path)


def clean_title(title, exe_base_name="", cfg=None):
    """제로폭 문자 제거, 공백 정리, mask_titles 치환, 길이 제한."""
    cfg = cfg or load_config()
    t = title or ""
    t = _ZERO_WIDTH.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    masks = cfg.get("mask_titles") or {}
    for k, v in masks.items():
        if exe_base_name and exe_base_name.lower() == str(k).lower():
            return v
    if len(t) > 300:
        t = t[:300]
    return t


# ── 예산 (§21): "9000 점심" 같은 한 줄에서 금액과 메모를 가른다. 순수 함수 ──
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|천|원)?")


def parse_amount(text):
    """'9,000원 점심' → (9000, '점심'), '1.2만 책' → (12000, '책'), 숫자 없으면 (None, text)."""
    text = (text or "").strip()
    m = _AMOUNT_RE.search(text)
    if not m:
        return None, text
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit == "만":
        num *= 10000
    elif unit == "천":
        num *= 1000
    amount = int(round(num))
    rest = (text[:m.start()] + " " + text[m.end():]).strip()
    rest = re.sub(r"\s+", " ", rest)
    return amount, rest
