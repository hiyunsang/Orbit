# -*- coding: utf-8 -*-
"""
plans.py — 내 일정·마감 (DESIGN §28). 표 이름은 plans — events 는 수집기의 원본 로그 표라 쓸 수 없다

"정밀공학회 구두발표 초록 내야 해" 같은 한 줄을 받아
  1) claude -p 로 제목·종류·날짜(글에 있으면)·반복을 가르고,
  2) 날짜가 없고 학회·학교 같은 공개 일정이면 claude -p + 웹 검색으로 마감일을 찾는다(공식 페이지 근거 필수, 못 찾으면 null).
행은 받자마자 저장되고(status=parsing) 뒤 스레드가 채운다. 날짜가 unknown 이면 화면에서 사용자가 적는다.

표준 라이브러리만, win32 없음. claude 호출은 notices.py 와 같은 방식(구독 CLI, 창 안 띄움).
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time

import common

KINDS = ("deadline", "task", "event")
KIND_NAME = {"deadline": "마감", "task": "할 일", "event": "일정"}
REPEATS = ("", "weekly")
CLAUDE_MODEL = "opus"
PARSE_TIMEOUT = 120
LOOKUP_TIMEOUT = 320
LOOKUP_MAX_TURNS = 20
STALE_SEC = 15 * 60          # 이 시간 넘게 parsing 이면 실패로 본다(서버 재시작 등)
ASYNC = True                 # 테스트에서 False 로 두면 같은 스레드에서 바로 채운다
parser = None                # 테스트 주입: parser(text, today) -> dict
looker = None                # 테스트 주입: looker(query, today) -> dict
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WD = "월화수목금토일"


def valid_date(s):
    if not isinstance(s, str) or not _DATE_RE.match(s):
        return False
    try:
        datetime.datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _wd(day):
    return _WD[datetime.datetime.strptime(day, "%Y-%m-%d").weekday()]


# ── claude ─────────────────────────────────────────────────────────
def _find_claude():
    for c in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(c)
        if p:
            return p
    return None


def _no_window():
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            kw["startupinfo"] = si
        except Exception:
            pass
    return kw


def _claude_json(prompt, timeout, tools=None, max_turns=None):
    exe = _find_claude()
    if not exe:
        return None
    args = [exe, "-p", "--model", CLAUDE_MODEL, "--output-format", "text"]
    if tools:
        args += ["--allowedTools"] + list(tools)
    if max_turns:
        args += ["--max-turns", str(max_turns)]
    try:
        r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout, **_no_window())
        out = r.stdout.decode("utf-8", "replace")
        # 마지막 JSON 객체를 고른다(검색 과정 설명이 앞에 붙어 올 수 있다)
        cands = re.findall(r"\{[^{}]*\}", out, re.S)
        for c in reversed(cands):
            try:
                return json.loads(c)
            except Exception:
                continue
        return None
    except Exception:
        return None


def parse_prompt(text, today):
    return (
        "오늘은 %s(%s) 이다. 아래 한 줄은 한국 대학원생(기계공학)이 적은 일정·할 일·마감이다. JSON 한 줄로만 답하라.\n"
        "{\"title\": \"짧은 제목(명사형, 24자 이내. 예: 정밀공학회 구두발표 초록 제출)\", "
        "\"kind\": \"deadline|task|event\", "
        "\"day\": \"YYYY-MM-DD 또는 null\", \"time\": \"HH:MM 또는 null\", "
        "\"repeat\": \"none|weekly\", \"needs_lookup\": true 또는 false, \"lookup_query\": \"검색어 또는 빈 문자열\"}\n"
        "규칙:\n"
        "- kind: 제출·신청·접수처럼 기한이 있는 것은 deadline, 숙제·과제·해야 할 일은 task, 수업·회의·발표처럼 때가 정해진 일은 event.\n"
        "- day: 글에 날짜·요일·'다음주 금요일'·'24일' 같은 표현이 있으면 오늘(%s) 기준으로 계산해 적는다. 지난 날짜가 되면 다음 해당일로. 글에 없으면 null.\n"
        "- repeat: '매주'·'수업' 처럼 되풀이되는 것이 분명할 때만 weekly.\n"
        "- needs_lookup: day 가 null 이고, 학회·학교·공모·장학처럼 웹에 공식 일정이 있는 것이면 true. 개인 약속·숙제면 false.\n"
        "- lookup_query: needs_lookup 이면 연도를 포함한 한국어 검색어(예: 2026 한국정밀공학회 추계학술대회 초록 접수 마감).\n"
        "- 다른 말은 쓰지 않는다.\n\n[글] %s" % (today, _wd(today), today, text))


def lookup_prompt(query, today):
    return (
        "오늘은 %s 이다. 웹 검색으로 다음 일정의 날짜(마감일이면 마감일)를 찾아라: %s\n"
        "검색은 2~3번, 페이지 열기는 2번 이내로 끝내고 바로 답하라. 공식 페이지(학회·학교·주최 측)에서 확인한 것만 적고, "
        "확인하지 못했으면 day 를 null 로 둔다. 추측으로 날짜를 만들지 않는다.\n"
        "마지막 출력은 JSON 한 줄만: {\"day\": \"YYYY-MM-DD 또는 null\", \"title\": \"행사·일정 이름\", "
        "\"source_url\": \"확인한 페이지 주소\", \"confidence\": \"confirmed|guess|unknown\", "
        "\"note\": \"근거 한 문장(원문의 날짜 표기 포함)\"}" % (today, query))


def parse_text(text, today):
    if parser is not None:
        return parser(text, today) or {}
    return _claude_json(parse_prompt(text, today), PARSE_TIMEOUT) or {}


def lookup_date(query, today):
    if looker is not None:
        return looker(query, today) or {}
    return _claude_json(lookup_prompt(query, today), LOOKUP_TIMEOUT, tools=("WebSearch", "WebFetch"),
                        max_turns=LOOKUP_MAX_TURNS) or {}


# ── 순수 함수 ───────────────────────────────────────────────────────
def next_occurrence(day, repeat, today, skip_until=None):
    """되풀이 일정의 다음 날짜. weekly 면 today(또는 skip_until) 이후 첫 같은 요일. 되풀이가 아니면 day 그대로."""
    if not valid_date(day):
        return None
    if repeat != "weekly":
        return day
    floor = today
    if valid_date(skip_until) and skip_until > floor:
        floor = skip_until
    d = datetime.datetime.strptime(day, "%Y-%m-%d").date()
    f = datetime.datetime.strptime(floor, "%Y-%m-%d").date()
    if d >= f:
        return day
    delta = (f - d).days
    return (d + datetime.timedelta(days=((delta + 6) // 7) * 7)).strftime("%Y-%m-%d")


def occurrences(day, repeat, start, end):
    """[start, end] 안의 발생일 목록. 최대 62일 범위를 가정."""
    if not valid_date(day) or start > end:
        return []
    if repeat != "weekly":
        return [day] if start <= day <= end else []
    first = next_occurrence(day, "weekly", start)
    out = []
    d = datetime.datetime.strptime(first, "%Y-%m-%d").date()
    e = datetime.datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e and len(out) < 60:
        out.append(d.strftime("%Y-%m-%d"))
        d += datetime.timedelta(days=7)
    return out


def days_between(a, b):
    return (datetime.datetime.strptime(b, "%Y-%m-%d").date() - datetime.datetime.strptime(a, "%Y-%m-%d").date()).days


# ── DB ──────────────────────────────────────────────────────────────
def _row(r, today=None, now=None):
    now = now or time.time()
    d = {
        "id": r["id"], "text": r["text"], "title": r["title"] or r["text"], "day": r["day"], "time": r["time"],
        "kind": r["kind"] or "task", "kind_name": KIND_NAME.get(r["kind"] or "task", "일정"),
        "repeat": r["repeat"] or "", "source_url": r["source_url"] or "", "confidence": r["confidence"] or "",
        "note": r["note"] or "", "status": r["status"], "done": int(r["done"] or 0),
        "skip_until": r["skip_until"], "created_ts": r["created_ts"], "done_ts": r["done_ts"],
    }
    if d["status"] == "parsing" and now - float(r["updated_ts"] or r["created_ts"] or now) > STALE_SEC:
        d["status"] = "failed"          # 찾는 중에 서버가 꺼졌거나 너무 오래 걸림
    if today:
        occ = next_occurrence(d["day"], d["repeat"], today, d["skip_until"]) if d["day"] else None
        d["occ"] = occ
        d["d"] = days_between(today, occ) if occ else None
    return d


def add(conn, text, day=None, cfg=None, today=None):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("빈 글")
    today = today or common.today_str(cfg)
    now = time.time()
    if day and valid_date(day):
        cur = conn.execute(
            "INSERT INTO plans(text, title, day, kind, repeat, confidence, status, created_ts, updated_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?)", (text, text[:60], day, "task", "", "confirmed", "ready", now, now))
        conn.commit()
        return cur.lastrowid, False
    cur = conn.execute(
        "INSERT INTO plans(text, title, kind, repeat, status, created_ts, updated_ts) VALUES(?,?,?,?,?,?,?)",
        (text, text[:60], "task", "", "parsing", now, now))
    conn.commit()
    eid = cur.lastrowid
    if ASYNC:
        threading.Thread(target=resolve, args=(eid, text, today, cfg), daemon=True).start()
    else:
        resolve(eid, text, today, cfg, conn=conn)
    return eid, True


def resolve(eid, text, today, cfg=None, conn=None):
    """parsing 행을 채운다. 실패해도 행은 남고 status=failed 로 — 사용자가 날짜를 적으면 된다."""
    own = conn is None
    if own:
        conn = common.connect(cfg)
    try:
        p = parse_text(text, today)
        title = str(p.get("title") or text)[:60]
        kind = p.get("kind") if p.get("kind") in KINDS else "task"
        repeat = "weekly" if p.get("repeat") == "weekly" else ""
        day = p.get("day") if valid_date(p.get("day")) else None
        tm = p.get("time") if isinstance(p.get("time"), str) and re.match(r"^\d{2}:\d{2}$", p.get("time")) else None
        src, conf, note = "", "confirmed" if day else "unknown", ""
        if day is None and p.get("needs_lookup") and p.get("lookup_query"):
            conn.execute("UPDATE plans SET title=?, kind=?, repeat=?, note=?, updated_ts=? WHERE id=?",
                         (title, kind, repeat, "날짜를 웹에서 찾는 중", time.time(), eid))
            conn.commit()
            lk = lookup_date(str(p.get("lookup_query")), today)
            if valid_date(lk.get("day")):
                day = lk.get("day")
                conf = lk.get("confidence") if lk.get("confidence") in ("confirmed", "guess") else "guess"
            else:
                conf = "unknown"
            src = str(lk.get("source_url") or "")[:300]
            note = str(lk.get("note") or "")[:300]
            if lk.get("title") and len(str(lk.get("title"))) < 60 and not day:
                pass
        status = "ready" if p else "failed"
        conn.execute(
            "UPDATE plans SET title=?, kind=?, repeat=?, day=?, time=?, source_url=?, confidence=?, note=?, status=?, updated_ts=? WHERE id=?",
            (title, kind, repeat, day, tm, src, conf, note, status, time.time(), eid))
        conn.commit()
    except Exception as e:  # 어떤 실패도 행을 잃지 않는다
        try:
            conn.execute("UPDATE plans SET status='failed', note=?, updated_ts=? WHERE id=?",
                         ("처리 실패: %s" % str(e)[:120], time.time(), eid))
            conn.commit()
        except Exception:
            pass
    finally:
        if own:
            conn.close()


def upcoming(conn, today, horizon_days=120, cfg=None):
    """안 끝난 것 전부(날짜 없는 것 포함) + 최근 7일 안에 끝낸 것. 순서: 날짜 없음(찾는 중·확인 필요) → 가까운 날짜순."""
    rows = [_row(r, today) for r in conn.execute("SELECT * FROM plans ORDER BY id").fetchall()]
    out = []
    for d in rows:
        if d["done"]:
            if d["done_ts"] and time.time() - d["done_ts"] < 7 * 86400:
                out.append(d)
            continue
        if d["occ"] and d["d"] is not None and d["d"] > horizon_days:
            continue
        out.append(d)

    def key(x):
        if x["done"]:
            return (3, 0, x["id"])
        if not x["occ"]:
            return (0 if x["status"] == "parsing" else 1, 0, x["id"])
        return (2, x["d"], x["id"])
    out.sort(key=key)
    return out


def in_range(conn, start, end, today):
    """[start, end] 날짜별 발생 — 주·월 보기용. {date: [{id, title, kind, done}]}"""
    by = {}
    for r in conn.execute("SELECT * FROM plans WHERE day IS NOT NULL").fetchall():
        d = _row(r, today)
        for occ in occurrences(d["day"], d["repeat"], start, end):
            if d["repeat"] == "weekly" and d["skip_until"] and occ < d["skip_until"]:
                continue
            by.setdefault(occ, []).append({"id": d["id"], "title": d["title"], "kind": d["kind"], "done": d["done"],
                                          "confidence": d["confidence"]})
    return by
