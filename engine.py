# -*- coding: utf-8 -*-
"""
engine.py — MAENG_worklog 엔진 (순수 함수, I/O 없음, win32 import 없음)

이벤트(events) + 세션(sessions) + 정정(overrides) + 규칙(rules) → 하루 구간(spans) + 요약(summary).
DESIGN.md §7 의 알고리즘 1~8 을 그 순서대로 구현하고, §11.1 의 9 단계(absent 변환)를 요약 계산 직전에 넣는다.

구현 메모
- classify: target='exe' 는 common.exe_base(exe) 와, target='title' 은 title 과 비교.
  is_regex 면 re.search(pattern, subject, re.I), 아니면 대소문자 무시 **부분 문자열 포함**(pattern in subject).
  즉 exe 규칙 "chrome" 은 "chrome.exe" 에도 매치된다. 잘못된 정규식은 무시(매치 실패 취급).
- 상태기계에 'stopped' 플래그를 둔다. 'stop' 이벤트 이후(다음 'start' 전까지)는 세션 커버리지가
  heartbeat_sec 만큼 더 남아 있어도 'off' 로 본다(트래커가 종료된 뒤 빈 exe 로 unknown 구간이 생기는 것 방지).
- idle_on 은 소급된 ts 를 가지므로 (ts, id) 정렬 후엔 idle_on 이 app 보다 먼저 올 수 있다.
  idle 상태에서 app 이벤트가 오면 idle_since 는 유지하고 exe/title 만 바꾼다.
  단, 정렬 전에 idle_on.ts 를 기록 순서상 직전 start/idle_off 의 ts 이상으로 clamp 한다(_clamp_idle_on).
- 각 구간은 내부에 앱별 시간 조각(segs)을 갖고 있어 병합·흡수·override 분할 뒤에도
  apps 초가 정확히 보존된다(분할된 조각은 실제로 그 시간대에 떠 있던 앱만 갖는다).
"""
from functools import lru_cache
import re

from common import (
    ACTIVE_CATEGORIES,
    CAT,
    CATEGORIES,
    MANUAL_CATEGORIES,
    DEFAULT_CONFIG,
    day_window,
    date_of_ts,
    exe_base,
)

_EPS = 1e-6
_NO_ABSORB = ("off", "paused")
_FOCUS_RUN = ("focus", "experiment")
# §19 '밤에 연구했다'로 셀 카테고리. ACTIVE_CATEGORIES 를 쓰지 않는 이유는 딴짓·휴식·중립·미분류까지
# 세면 밤 8시에 유튜브 30분 본 것이 '연구'가 되기 때문이다.
_NIGHT_WORK = ("focus", "experiment", "meeting")


def night_research_sec(spans, start_ts, end_ts):
    """§19 spans(build_day 결과) 중 집중·실험·회의가 [start_ts, end_ts) 와 겹친 초의 합.

    사용자가 정정(override)한 구간은 이미 spans 에 반영돼 있으므로 정정이 곧 이 값의 정정이다
    — 두 번째 진실을 만들지 않는다.
    """
    total = 0.0
    for sp in spans or ():
        if sp.get("category") not in _NIGHT_WORK:
            continue
        a = max(float(sp["start"]), float(start_ts))
        b = min(float(sp["end"]), float(end_ts))
        if b > a:
            total += b - a
    return total


# ------------------------------------------------------------------ 분류
@lru_cache(maxsize=1024)
def _compile(pattern):
    """정규식 컴파일 (대소문자 무시). 잘못된 패턴이면 None."""
    try:
        return re.compile(pattern, re.I)
    except re.error:
        return None


def _match(pattern, is_regex, subject):
    if not pattern or not subject:
        return False
    if is_regex:
        rx = _compile(pattern)
        if rx is None:
            return False
        return rx.search(subject) is not None
    return pattern.lower() in subject.lower()


def classify(exe, title, rules):
    """(category_id, rule|None). rules 는 priority 내림차순으로 정렬돼 있다고 가정. 첫 매치 반환."""
    base = exe_base(exe or "")
    title = title or ""
    if not base and not title:
        return ("unknown", None)
    for r in rules or ():
        if not isinstance(r, dict):
            continue
        if not r.get("enabled", True):
            continue
        target = r.get("target", "title")
        if target == "exe":
            subject = base
        elif target == "title":
            subject = title
        else:
            continue
        pattern = r.get("pattern")
        if pattern is None:
            continue
        if _match(str(pattern), bool(r.get("is_regex", False)), subject):
            cat = r.get("category") or "unknown"
            if cat not in CAT:
                cat = "unknown"
            return (cat, r)
    return ("unknown", None)


# ------------------------------------------------------------------ 표시 이름 (DESIGN §15.2)
_NAME_MAX = 40
_WS = re.compile(r"\s+")


def _prep_names(names):
    """common.normalize_names 형태로 맞춘다(None → 빈 규칙). 이미 정규화된 dict 는 값이 그대로 통과한다."""
    if not names:
        return {"browsers": frozenset(), "strip": (), "title": (), "exe": {}}
    if not isinstance(names, dict):
        return {"browsers": frozenset(), "strip": (), "title": (), "exe": {}}
    browsers = frozenset(str(b).strip().lower() for b in (names.get("browsers") or ()) if b)
    strip = tuple(s for s in (names.get("strip") or ()) if isinstance(s, str) and s)
    title = []
    for r in names.get("title") or ():
        if not isinstance(r, dict):
            continue
        pat = r.get("pattern")
        name = r.get("name")
        if not pat or not name:
            continue
        exe = r.get("exe")
        title.append((str(pat), bool(r.get("is_regex", False)), str(name),
                      str(exe).strip().lower() if exe else None))
    exe_map = {}
    for k, v in (names.get("exe") or {}).items():
        if k and v:
            exe_map[str(k).strip().lower()] = str(v)
    return {"browsers": browsers, "strip": strip, "title": tuple(title), "exe": exe_map}


def _strip_exe_suffix(base):
    if base.lower().endswith(".exe"):
        return base[:-4]
    return base


def _display_name_prepped(exe_b, title, pn):
    """exe_b 는 이미 basename. pn 은 _prep_names 결과."""
    exe_l = exe_b.lower()
    title = title or ""
    # 1. title 규칙 (브라우저 탭·Athenaeum 리더·앱 창 모두)
    for pat, is_regex, name, only_exe in pn["title"]:
        if only_exe is not None and only_exe != exe_l:
            continue
        if _match(pat, is_regex, title):
            return name
    exe_map = pn["exe"]
    # 2. 브라우저: 제목에서 strip 정규식 제거 → 남은 문자열(40자)
    if exe_l in pn["browsers"]:
        t = title
        for pat in pn["strip"]:
            rx = _compile(pat)
            if rx is None:
                continue
            t = rx.sub("", t)
        t = _WS.sub(" ", t).strip()
        if t:
            if len(t) > _NAME_MAX:
                t = t[:_NAME_MAX - 1].rstrip() + u"…"
            return t
        return exe_map.get(exe_l) or "Chrome"
    # 3. exe 표
    if exe_l in exe_map:
        return exe_map[exe_l]
    # 4. exe 에서 .exe 뗀 것
    if not exe_b:
        return u"(창 없음)"
    return _strip_exe_suffix(exe_b)


def display_name(exe, title, names=None):
    """(exe 또는 basename, 창 제목, names 규칙) → 사람이 읽을 표시 이름. names 가 None 이면 exe 에서 .exe 를 뗀 값.

    1. names['title'] 규칙을 위에서부터(대소문자 무시, substring/regex; 'exe' 가 있으면 그 exe 일 때만) → 첫 매치의 name
    2. exe 가 names['browsers'] 면 제목에서 names['strip'] 정규식을 순서대로 지우고 공백 정리 → 40자 (비면 exe 표의 브라우저 이름, 없으면 'Chrome')
    3. names['exe'] 표에 있으면 그 이름
    4. 그 외 exe 에서 .exe 를 뗀 것. exe 도 비면 '(창 없음)'
    잘못된 정규식은 무시(매치 실패 / strip 건너뜀)."""
    return _display_name_prepped(exe_base(exe or ""), title, _prep_names(names))


# ------------------------------------------------------------------ 상태기계
class _State(object):
    __slots__ = ("exe", "title", "idle_since", "locked", "remote", "paused", "stopped")

    def __init__(self):
        self.reset()
        self.stopped = False

    def reset(self):
        self.exe = ""
        self.title = ""
        self.idle_since = None
        self.locked = False
        self.remote = False
        self.paused = False

    def apply(self, ev):
        kind = ev.get("kind")
        ts = float(ev.get("ts") or 0.0)
        if kind == "start":
            self.reset()
            self.stopped = False
        elif kind == "stop":
            self.reset()
            self.stopped = True
        elif kind == "app":
            self.exe = ev.get("exe") or ""
            self.title = ev.get("title") or ""
        elif kind == "idle_on":
            if self.idle_since is None or ts < self.idle_since:
                self.idle_since = ts
        elif kind == "idle_off":
            self.idle_since = None
        elif kind == "lock":
            self.locked = True
        elif kind == "unlock":
            self.locked = False
        elif kind == "remote_on":
            self.remote = True
        elif kind == "remote_off":
            self.remote = False
        elif kind == "pause":
            self.paused = True
        elif kind == "resume":
            self.paused = False
        # 알 수 없는 kind 는 무시

    def snapshot(self):
        return (self.exe, self.title, self.idle_since, self.locked,
                self.remote, self.paused, self.stopped)


_IDLE_FLOOR_KINDS = ("start", "idle_off")


def _clamp_idle_on(evs):
    """idle_on 의 소급 ts 를 '기록 순서(id)상 직전 start / idle_off 의 ts' 이상으로 올린다.

    트래커는 idle_off 를 폴링 시각(now)으로, 다음 idle_on 을 마지막 입력 시각(now-idle_ms)으로
    기록하므로 입력이 한 번뿐이면 idle_on.ts < 직전 idle_off.ts 가 된다. 그대로 (ts,id) 정렬하면
    idle_on 이 idle_off 앞에 와서 idle_off 가 idle 을 지워 버린다(자리비움 통째로 누락).
    트래커 시작 전 마지막 입력으로 소급된 idle_on(ts < start.ts) 도 start 의 reset 에 지워지므로 같이 막는다.
    resume/unlock 은 floor 로 쓰지 않는다(일시정지·잠금 중 떠난 소급 idle 은 유지돼야 함).
    입력 dict 는 수정하지 않고 복사본을 만든다."""
    def id_key(ev):
        idv = ev.get("id")
        return int(idv) if idv is not None else 0
    out = []
    floor = None
    for ev in sorted(evs, key=id_key):
        kind = ev.get("kind")
        if kind == "idle_on" and floor is not None:
            try:
                ts = float(ev.get("ts"))
            except (TypeError, ValueError):
                ts = None
            if ts is not None and ts < floor:
                ev = dict(ev)
                ev["ts"] = floor
        elif kind in _IDLE_FLOOR_KINDS:
            try:
                floor = float(ev.get("ts"))
            except (TypeError, ValueError):
                pass
        out.append(ev)
    return out


def _sorted_events(events):
    def key(ev):
        ts = ev.get("ts")
        idv = ev.get("id")
        return (float(ts if ts is not None else 0.0), int(idv) if idv is not None else 0)
    evs = [e for e in (events or ()) if isinstance(e, dict) and e.get("ts") is not None]
    evs = _clamp_idle_on(evs)
    evs.sort(key=key)
    return evs


def _raw_segments(events, wstart, tail_end):
    """1. 원시 구간: [(a, b, state_snapshot), ...] window 로 클립."""
    st = _State()
    segs = []
    seg_start = float("-inf")
    for ev in _sorted_events(events):
        t = float(ev["ts"])
        if t >= tail_end:
            break
        a = max(seg_start, wstart)
        if t > a:
            segs.append((a, t, st.snapshot()))
        st.apply(ev)
        seg_start = t
    a = max(seg_start, wstart)
    if tail_end > a:
        segs.append((a, tail_end, st.snapshot()))
    return segs


def _coverage(sessions, cfg, now_ts, wstart, tail_end):
    """2. 세션 커버리지 구간 목록(정렬·병합, window 클립)."""
    hb = float(cfg.get("heartbeat_sec", DEFAULT_CONFIG["heartbeat_sec"]))
    dead = float(cfg.get("dead_after_sec", DEFAULT_CONFIG["dead_after_sec"]))
    ivs = []
    for s in sessions or ():
        if not isinstance(s, dict) or s.get("start_ts") is None:
            continue
        st = float(s["start_ts"])
        last = s.get("last_seen")
        end_ts = s.get("end_ts")
        if end_ts is None:
            if last is not None and (now_ts - float(last)) < dead:
                cov_end = float(now_ts)
            else:
                cov_end = (float(last) if last is not None else st) + hb
        else:
            cov_end = float(end_ts) + hb
        a = max(st, wstart)
        b = min(cov_end, tail_end)
        if b > a:
            ivs.append((a, b))
    ivs.sort()
    merged = []
    for a, b in ivs:
        if merged and a <= merged[-1][1] + _EPS:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _split_by_coverage(segs, cov):
    """세션 경계에서 자르고 커버 여부를 붙인다: [(a, b, state, covered)]"""
    out = []
    for a, b, st in segs:
        pos = a
        for ca, cb in cov:
            if cb <= pos:
                continue
            if ca >= b:
                break
            if ca > pos:
                out.append((pos, ca, st, False))
                pos = ca
            e = min(cb, b)
            if e > pos:
                out.append((pos, e, st, True))
                pos = e
            if pos >= b:
                break
        if pos < b:
            out.append((pos, b, st, False))
    return out


# ------------------------------------------------------------------ 조각(piece)
def _piece(start, end, category, exe_full="", title="", with_apps=True):
    base = exe_base(exe_full) if exe_full else ""
    title = title or ""
    segs = []
    if with_apps and (base or title):
        segs.append((start, end, (base, title)))
    return {
        "start": start,
        "end": end,
        "category": category,
        "segs": segs,  # [(a, b, (exe_base, title))] 앱별 시간 조각 — apps 는 여기서 계산
        "source": "auto",
        "override_id": None,
    }


def _grace_for(category, rule, cfg):
    if rule is not None and rule.get("idle_limit_sec") is not None:
        try:
            return float(rule["idle_limit_sec"])
        except (TypeError, ValueError):
            pass
    grace = cfg.get("idle_grace_sec") or {}
    try:
        return float(grace.get(category, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _categorize(parts, rules, cfg):
    """3. 구간 카테고리 결정 → piece 목록"""
    pieces = []
    for a, b, st, covered in parts:
        exe, title, idle_since, locked, remote, paused, stopped = st
        if not covered or stopped:
            pieces.append(_piece(a, b, "off", with_apps=False))
            continue
        if paused:
            pieces.append(_piece(a, b, "paused", with_apps=False))
            continue
        if remote:
            pieces.append(_piece(a, b, "remote", with_apps=False))
            continue
        if locked:
            pieces.append(_piece(a, b, "away", with_apps=False))
            continue
        c, rule = classify(exe, title, rules)
        if idle_since is not None:
            cutoff = float(idle_since) + _grace_for(c, rule, cfg)
            if b <= cutoff + _EPS:
                pieces.append(_piece(a, b, c, exe, title))
            elif a >= cutoff - _EPS:
                pieces.append(_piece(a, b, "away", with_apps=False))
            else:
                pieces.append(_piece(a, cutoff, c, exe, title))
                pieces.append(_piece(cutoff, b, "away", with_apps=False))
            continue
        pieces.append(_piece(a, b, c, exe, title))
    return [p for p in pieces if p["end"] - p["start"] > 0]


def _count_distraction_entries(pieces):
    """4. 딴짓 진입 횟수 (흡수 전 원시 구간, 직전 **active** 구간 기준 — DESIGN §7-4).
    away/paused/remote/off 는 건너뛴다: 딴짓 중 자리를 비웠다 같은 딴짓으로 돌아온 것은 새 진입이 아니다."""
    n = 0
    prev = None
    for p in pieces:
        c = p["category"]
        if c not in ACTIVE_CATEGORIES:
            continue
        if c == "distraction" and prev != "distraction":
            n += 1
        prev = c
    return n


def _apps_of(segs):
    """앱 조각 → {(exe_base, title): sec}"""
    apps = {}
    for a, b, k in segs:
        if b > a:
            apps[k] = apps.get(k, 0.0) + (b - a)
    return apps


def _adjacent(p, q):
    return abs(p["end"] - q["start"]) < _EPS


def _absorb(pieces, absorb_sec):
    """5. 길이 < absorb_sec 인 구간을 이전(없거나 off/paused 면 다음) 구간에 흡수.
    다음 구간이 off 이면 흡수하지 않고 그대로 둔다. 다음이 paused 이면 시간만 paused 에 붙인다."""
    out = []
    pending = None
    for p in pieces:
        p = dict(p, segs=list(p["segs"]))
        if pending is not None:
            if p["category"] == "off" or not _adjacent(pending, p):
                out.append(pending)
            elif p["category"] == "paused":
                # 트래커 시작 직후 곧바로 일시정지되는 경우(start→app→pause 가 수십 ms 안에 옴)
                # 남는 수 ms 짜리 unknown/앱 조각은 paused 에 붙인다(앱 조각은 버림 — paused 는 apps 없음).
                p["start"] = pending["start"]
            else:
                p["start"] = pending["start"]
                p["segs"] = pending["segs"] + p["segs"]
            pending = None
        short = (p["category"] not in _NO_ABSORB) and (p["end"] - p["start"] < absorb_sec)
        if not short:
            out.append(p)
            continue
        if out and out[-1]["category"] not in _NO_ABSORB and _adjacent(out[-1], p):
            out[-1]["end"] = p["end"]
            out[-1]["segs"].extend(p["segs"])
        else:
            pending = p
    if pending is not None:
        out.append(pending)
    return out


def _merge_same(pieces, key):
    """6./7. 인접(끝==시작)하고 key 가 같은 구간 병합"""
    out = []
    for p in pieces:
        p = dict(p, segs=list(p["segs"]))
        if out and _adjacent(out[-1], p) and key(out[-1]) == key(p):
            out[-1]["end"] = p["end"]
            out[-1]["segs"].extend(p["segs"])
        else:
            out.append(p)
    return out


def _scaled(p, a, b):
    """p 를 [a,b] 로 잘라낸 복사본. 앱 조각도 같은 범위로 클립(초 합계 보존)."""
    q = dict(p)
    q["start"] = a
    q["end"] = b
    segs = []
    for sa, sb, k in p["segs"]:
        ca = max(sa, a)
        cb = min(sb, b)
        if cb > ca:
            segs.append((ca, cb, k))
    q["segs"] = segs
    return q


def _apply_overrides(pieces, overrides, wstart, tail_end):
    """7. overrides 적용 (created_ts 오름차순, 나중 것이 이김). off 는 바꾸지 않음."""
    ovs = []
    for o in overrides or ():
        if not isinstance(o, dict):
            continue
        label = o.get("label")
        if label not in MANUAL_CATEGORIES:
            continue
        if o.get("start_ts") is None or o.get("end_ts") is None:
            continue
        a = max(float(o["start_ts"]), wstart)
        b = min(float(o["end_ts"]), tail_end)
        if b <= a:
            continue
        ovs.append((float(o.get("created_ts") or 0.0), int(o.get("id") or 0), a, b, label, o.get("id")))
    ovs.sort(key=lambda x: (x[0], x[1]))

    for _created, _oid_sort, oa, ob, label, oid in ovs:
        out = []
        for p in pieces:
            if p["category"] == "off" or p["end"] <= oa + _EPS or p["start"] >= ob - _EPS:
                out.append(p)
                continue
            ia = max(p["start"], oa)
            ib = min(p["end"], ob)
            if ia > p["start"] + _EPS:
                out.append(_scaled(p, p["start"], ia))
            mid = _scaled(p, ia, ib)
            mid["category"] = label
            mid["source"] = "override"
            mid["override_id"] = oid
            out.append(mid)
            if p["end"] > ib + _EPS:
                out.append(_scaled(p, ib, p["end"]))
        pieces = [q for q in out if q["end"] - q["start"] > 0]
    return pieces


# ------------------------------------------------------------------ 출력·요약
def _top_apps(apps, n, pn=None):
    """상위 n개 앱. pn(_prep_names 결과)이 있으면 각 항목에 name(표시 이름)을 붙인다."""
    items = sorted(apps.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out = []
    for k, v in items[:n]:
        item = {"exe": k[0], "title": k[1], "sec": round(v, 1)}
        if pn is not None:
            item["name"] = _display_name_prepped(k[0], k[1], pn)
        out.append(item)
    return out


def _top_apps_by_name(apps, n, pn):
    """미분류 Top n: (exe, 표시 이름) 단위로 합산한 상위 n개. 대표 title 은 그 이름 안에서 가장 긴 (exe,title).
    제목이 매번 바뀌는 사이트(네이버 블로그 글마다 다른 제목)가 여러 행으로 쪼개져 순위가 왜곡되는 것을 막는다.
    exe 를 키에 남기는 이유: 팝오버의 '이 프로그램(exe)은 앞으로 항상' 규칙이 대표 exe 를 쓰므로 exe 가 다른 것은 합치지 않는다.
    pn 이 None 이면 _top_apps 와 같다((exe,title) 단위)."""
    if pn is None:
        return _top_apps(apps, n)
    groups = {}
    for (exe_b, title), sec in apps.items():
        if sec <= 0:
            continue
        name = _display_name_prepped(exe_b, title, pn)
        g = groups.get((exe_b, name))
        if g is None:
            g = groups[(exe_b, name)] = {"sec": 0.0, "best": None}
        g["sec"] += sec
        if g["best"] is None or sec > g["best"][1] or (sec == g["best"][1] and title < g["best"][0]):
            g["best"] = (title, sec)
    items = sorted(groups.items(), key=lambda kv: (-kv[1]["sec"], kv[0][0], kv[0][1]))
    out = []
    for (exe_b, name), g in items[:n]:
        out.append({"exe": exe_b, "title": g["best"][0], "sec": round(g["sec"], 1), "name": name})
    return out


def _to_span(p, pn=None):
    top = _top_apps(_apps_of(p["segs"]), 3, pn)
    span = {
        "start": p["start"],
        "end": p["end"],
        "category": p["category"],
        "exe": top[0]["exe"] if top else None,
        "title": top[0]["title"] if top else None,
        "source": p["source"],
        "override_id": p["override_id"],
        "apps": top,
    }
    if pn is not None:
        # label = 대표 app(1위)의 표시 이름. apps 가 없는 구간(off/paused/absent 등)은 exe/title 처럼 None.
        span["label"] = top[0]["name"] if top else None
    return span


def _is_in_progress(date_str, cfg, now_ts, sessions):
    """오늘이고 마지막 세션이 살아있으면 True (DESIGN §7 summary.in_progress)."""
    dead = float(cfg.get("dead_after_sec", DEFAULT_CONFIG["dead_after_sec"]))
    alive = False
    for s in sessions or ():
        if isinstance(s, dict) and s.get("end_ts") is None and s.get("last_seen") is not None:
            if now_ts - float(s["last_seen"]) < dead:
                alive = True
                break
    return bool(alive) and (date_str == date_of_ts(now_ts, cfg))


def _active_bounds(pieces):
    """(first_active, last_active): kind=active 구간의 최소 start / 최대 end. 없으면 (None, None)."""
    first_active = None
    last_active = None
    for p in pieces:
        if p["category"] in ACTIVE_CATEGORIES:
            if first_active is None or p["start"] < first_active:
                first_active = p["start"]
            if last_active is None or p["end"] > last_active:
                last_active = p["end"]
    return first_active, last_active


def _convert_absent(pieces, first_active, last_active, in_progress):
    """9. absent 변환 (DESIGN §11.1) — 출근 전·퇴근 후에 PC 만 켜져 있던 away 를 absent 로.

    - first_active 가 None(활동 없음)이면 모든 away → absent.
    - end <= first_active 인 away → absent.
    - in_progress 가 아니면 start >= last_active 인 away 도 → absent (오늘 진행 중이면 뒤쪽 away 는 그대로).
    - lock/idle 로 생긴 away 모두 대상. paused/remote/off 는 건드리지 않는다. active 구간도 바꾸지 않는다.
    입력 dict 는 수정하지 않고 복사본을 만든다."""
    out = []
    for p in pieces:
        if p["category"] == "away":
            to_absent = False
            if first_active is None:
                to_absent = True
            elif p["end"] <= first_active + _EPS:
                to_absent = True
            elif (not in_progress) and last_active is not None and p["start"] >= last_active - _EPS:
                to_absent = True
            if to_absent:
                p = dict(p, category="absent")
        out.append(p)
    return out


def _summary(pieces, date_str, cfg, now_ts, sessions, wend, tail_end, in_progress=None, pn=None):
    """8. 요약. pn(_prep_names 결과)이 있으면 unknown_top 항목에 name 을 붙인다."""
    if in_progress is None:
        in_progress = _is_in_progress(date_str, cfg, now_ts, sessions)

    by_cat = dict((c["id"], 0.0) for c in CATEGORIES)
    for p in pieces:
        c = p["category"]
        by_cat[c] = by_cat.get(c, 0.0) + (p["end"] - p["start"])
    first_active, last_active = _active_bounds(pieces)

    active_sec = sum(by_cat.get(c, 0.0) for c in ACTIVE_CATEGORIES)
    distraction_sec = by_cat.get("distraction", 0.0)
    ratio = (distraction_sec / active_sec) if active_sec > 0 else 0.0

    attend_sec = 0.0
    off_sec = 0.0
    if first_active is not None:
        attend_end = min(now_ts, wend) if in_progress else last_active
        if attend_end < last_active:
            attend_end = last_active
        attend_sec = max(0.0, attend_end - first_active)
        for p in pieces:
            if p["category"] != "off":
                continue
            a = max(p["start"], first_active)
            b = min(p["end"], attend_end)
            if b > a:
                off_sec += b - a

    focus_longest = 0.0
    run = 0.0
    prev = None
    for p in pieces:
        if p["category"] in _FOCUS_RUN:
            if prev is not None and prev["category"] in _FOCUS_RUN and _adjacent(prev, p):
                run += p["end"] - p["start"]
            else:
                run = p["end"] - p["start"]
            if run > focus_longest:
                focus_longest = run
        else:
            run = 0.0
        prev = p

    unknown_segs = []
    for p in pieces:
        if p["category"] == "unknown":
            unknown_segs.extend(p["segs"])
    unknown_apps = _apps_of(unknown_segs)

    r1 = lambda x: round(float(x), 1)
    return {
        "first_active": first_active,
        "last_active": last_active,
        "attend_sec": r1(attend_sec),
        "active_sec": r1(active_sec),
        "by_category": dict((k, r1(v)) for k, v in by_cat.items()),
        "distraction_sec": r1(distraction_sec),
        "distraction_ratio": ratio,
        "distraction_entries": 0,  # 호출측에서 채움
        "away_sec": r1(by_cat.get("away", 0.0)),      # away 만 (absent 제외)
        "absent_sec": r1(by_cat.get("absent", 0.0)),
        "remote_sec": r1(by_cat.get("remote", 0.0)),
        "paused_sec": r1(by_cat.get("paused", 0.0)),
        "off_sec": r1(off_sec),
        "focus_longest_sec": r1(focus_longest),
        "unknown_top": _top_apps_by_name(unknown_apps, 5, pn),
        "in_progress": in_progress,
    }


def build_day(date_str, events, sessions, overrides, rules, cfg, now_ts, names=None):
    """DESIGN.md §7. 반환 형태는 그 문서 참고. 결정적(같은 입력 → 같은 출력).

    names (§15, 선택): common.load_names() 형태의 표시 이름 규칙. 주면 각 span 에 label(대표 app 의 display_name),
    apps[i].name, summary.unknown_top[i].name 을 채운다. None 이면 규칙 없음 → label 은 exe 에서 .exe 를 뗀 값.
    카테고리 로직에는 영향 없음."""
    cfg = cfg or DEFAULT_CONFIG
    now_ts = float(now_ts)
    pn = _prep_names(names)
    wstart, wend = day_window(date_str, cfg)
    tail_end = min(now_ts, wend)
    absorb_sec = float(cfg.get("absorb_sec", DEFAULT_CONFIG["absorb_sec"]))

    result = {
        "date": date_str,
        "window": {"start": wstart, "end": wend},
        "spans": [],
        "summary": None,
    }

    if tail_end <= wstart:
        pieces = []
        entries = 0
    else:
        # 1. 원시 구간
        segs = _raw_segments(events, wstart, tail_end)
        # 2. 세션 커버리지
        cov = _coverage(sessions, cfg, now_ts, wstart, tail_end)
        parts = _split_by_coverage(segs, cov)
        # 3. 카테고리
        pieces = _categorize(parts, rules, cfg)
        # 4. 딴짓 진입 횟수 (흡수 전)
        entries = _count_distraction_entries(pieces)
        # 5. 흡수
        pieces = _absorb(pieces, absorb_sec)
        # 6. 병합
        pieces = _merge_same(pieces, lambda p: p["category"])
        # 7. overrides
        pieces = _apply_overrides(pieces, overrides, wstart, tail_end)
        pieces = _merge_same(pieces, lambda p: (p["category"], p["source"], p["override_id"]))

    pieces = [p for p in pieces if p["end"] - p["start"] > 0]
    pieces.sort(key=lambda p: (p["start"], p["end"]))

    # 9. absent 변환 (DESIGN §11.1): first/last_active 와 in_progress 를 먼저 구한다(override 반영 후).
    #    absent 변환은 active 구간을 바꾸지 않으므로 요약의 first/last_active 는 변환 전후 동일하다.
    in_progress = _is_in_progress(date_str, cfg, now_ts, sessions)
    first_active, last_active = _active_bounds(pieces)
    pieces = _convert_absent(pieces, first_active, last_active, in_progress)
    pieces = _merge_same(pieces, lambda p: (p["category"], p["source"], p["override_id"]))

    # 8. 요약
    summary = _summary(pieces, date_str, cfg, now_ts, sessions, wend, tail_end, in_progress, pn)
    summary["distraction_entries"] = entries

    result["spans"] = [_to_span(p, pn) for p in pieces]
    result["summary"] = summary
    return result
