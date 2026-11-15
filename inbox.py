# -*- coding: utf-8 -*-
"""
inbox.py — 받아 적기 · 개인 비서 (DESIGN §51).

떠오르는 대로 적은 글(한 줄이든, 쉼표·줄바꿈으로 이어진 여러 생각이든)을 받자마자 inbox 표에 저장하고
(status=parsing) 뒤 스레드가 claude -p 로 나눠 제자리에 넣는다.
  - 날짜 없는 할 일 → todos(§20)          - 날짜 있는 할 일 · 마감 · 일정 → plans(§28)
  - 기억할 것(들은 말 · 번호 · 치수 · 아이디어) → keeps
  - 이미 있는 것을 끝냈다는 말 → 그 항목을 끝냄      - 같은 것을 또 적음 → 그대로 둔다
만든 것 · 바꾼 것은 inbox.result 에 적어 두어 '되돌리기' 가 그대로 되돌린다.
claude 가 없거나 실패하면 글 전체를 날짜 없는 할 일 하나로 남긴다 — 적은 글은 잃지 않는다.
분야(연구 · 수업 · 토플 · 행정 · 개인)와 '나중에' 표시는 item_meta 에(todos · plans 표는 바꾸지 않는다 — §0 DB 규칙).

표준 라이브러리만, win32 없음. claude 호출 · 웹에서 날짜 찾기는 plans.py 것을 쓴다.
"""
import datetime
import json
import re
import threading
import time

import common
import plans

TYPES = ("task", "deadline", "event", "note", "done", "same")
AREAS = ("연구", "수업", "토플", "행정", "개인")
CLASSIFY_TIMEOUT = 150
STALE_SEC = 5 * 60          # 이보다 오래 parsing 이면(서버가 꺼졌다 켜짐 등) 글을 할 일 하나로 살린다
SHOW_SEC = 10 * 60          # 나눈 결과를 화면에 남겨 두는 시간(확인을 누르면 바로 사라짐)
MAX_TEXT = 4000
CTX_MAX = 150               # claude 에게 보여 줄 '이미 있는 것' 줄 수
ASYNC = True                # 테스트에서 False 면 같은 스레드에서 바로 나눈다
classifier = None           # 테스트 주입: classifier(text, today, ctx_lines) -> {"items": [...]} 또는 None
_REF_RE = re.compile(r"^[tpk][1-9]\d{0,9}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
# claude 가 없을 때만 쓰는 옛 갈림(§28 의 프론트 정규식과 같다): 날짜 낱말이 있으면 일정, 아니면 할 일
NEEDS_PARSE = re.compile(r"\d|오늘|내일|모레|다음|이번|요일|매주|마감|제출|접수|신청|학회|수업|회의|발표|실습|까지")


def available():
    return classifier is not None or plans._find_claude() is not None


def _md(day):
    d = datetime.datetime.strptime(day, "%Y-%m-%d")
    return "%d/%d(%s)" % (d.month, d.day, plans._WD[d.weekday()])


# ── 분야 · 나중에 (item_meta) ────────────────────────────────────────
def meta_map(conn):
    return dict((r["ref"], {"area": r["area"] or "", "someday": int(r["someday"] or 0)})
                for r in conn.execute("SELECT ref, area, someday FROM item_meta"))


def set_meta(conn, ref, area=None, someday=None, inbox_id=None):
    """area · someday 중 준 것만 바꾼다. 둘 다 비면 행을 지운다."""
    row = conn.execute("SELECT * FROM item_meta WHERE ref=?", (ref,)).fetchone()
    a = (row["area"] if row else "") or ""
    sd = int(row["someday"] or 0) if row else 0
    ib = row["inbox_id"] if row else None
    if area is not None:
        a = str(area).strip()[:8]
    if someday is not None:
        sd = 1 if someday else 0
    if inbox_id is not None:
        ib = inbox_id
    if not a and not sd:
        conn.execute("DELETE FROM item_meta WHERE ref=?", (ref,))
    else:
        conn.execute("INSERT OR REPLACE INTO item_meta(ref, area, someday, inbox_id) VALUES(?,?,?,?)", (ref, a, sd, ib))


def drop_meta(conn, ref):
    conn.execute("DELETE FROM item_meta WHERE ref=?", (ref,))


# ── claude 에게 줄 것 ──────────────────────────────────────────────
def context_lines(conn, today, limit=CTX_MAX):
    """끝내지 않은 것 목록 — id 와 함께. claude 가 '끝냈다' · '이미 있다' 를 가리킬 때 이 id 를 쓴다."""
    meta = meta_map(conn)
    out = []
    for r in conn.execute(
            "SELECT t.id, t.text, t.day, (p.todo_id IS NOT NULL) AS doing FROM todos t "
            "LEFT JOIN todo_progress p ON p.todo_id=t.id WHERE t.done=0 ORDER BY doing DESC, t.id DESC"):
        ref = "t%d" % r["id"]
        bits = [r["text"], "할 일"]
        if r["doing"]:
            bits.append("하는 중")
        m = meta.get(ref)
        if m and m["area"]:
            bits.append(m["area"])
        out.append("%s %s" % (ref, " · ".join(bits)))
    for d in plans.upcoming(conn, today):
        if d["done"]:
            continue
        ref = "p%d" % d["id"]
        when = ("%s %s" % (d["kind_name"], _md(d["occ"]))) if d.get("occ") else ("%s · 날짜 미정" % d["kind_name"])
        bits = [d["title"], when]
        if d["repeat"] == "weekly":
            bits.append("매주")
        m = meta.get(ref)
        if m and m["area"]:
            bits.append(m["area"])
        out.append("%s %s" % (ref, " · ".join(bits)))
    for r in conn.execute("SELECT id, text, area FROM keeps ORDER BY id DESC LIMIT 40"):
        out.append("k%d %s · 기억%s" % (r["id"], r["text"], (" · " + r["area"]) if r["area"] else ""))
    return out[:limit]


def prompt(text, today, ctx):
    wd = plans._wd(today)
    have = "\n".join(ctx) if ctx else "(없음)"
    return (
        "오늘은 %s(%s요일) 이다. 너는 한국 대학원생(기계공학 석사, 연구실에서 장비 설계·발주·실험을 하고 수업과 토플 학원도 다님)의 "
        "개인 비서다. 이 사람은 머릿속에 떠오르는 할 일 · 일정 · 기억할 것을 아래 [글]처럼 아무렇게나 던져 둔다. "
        "그것을 나눠 정리하고 JSON 하나로만 답하라.\n\n"
        "[이미 있는 것] — 아직 안 끝낸 것. 맨 앞이 id (t=할 일, p=날짜 있는 할 일·마감·일정, k=기억)\n%s\n\n"
        "[출력] {\"items\": [{\"type\": \"task|deadline|event|note|done|same\", \"title\": \"…\", "
        "\"day\": \"YYYY-MM-DD 또는 null\", \"time\": \"HH:MM 또는 null\", \"repeat\": \"none|weekly\", "
        "\"area\": \"연구|수업|토플|행정|개인\", \"doing\": false, \"someday\": false, \"ref\": \"id 또는 null\", "
        "\"needs_lookup\": false, \"lookup_query\": \"\"}]}\n\n"
        "[나누는 법]\n"
        "- 한 가지 일 = 한 항목. 쉼표 · 줄바꿈 · '그리고' 로 서로 다른 일이 이어지면 나눈다. 한 일의 세부(누구에게 · 무엇을 · 몇 개 · 왜)는 "
        "나누지 말고 그 항목 title 에 남긴다.\n"
        "- task: 해야 할 행동(메일 · 연락 · 발주 · 구매 · 설계 · 수정 · 예약 · 준비 등). 날짜 표현이 있으면 day 를 채운다.\n"
        "- deadline: 제출 · 신청 · 접수 · 납부처럼 기한이 핵심인 것.\n"
        "- event: 수업 · 회의 · 미팅 · 약속 · 시험처럼 그 날 그 시각에 있는 일.\n"
        "- note: 행동이 아니라 기억해 둘 사실 · 정보 · 들은 말 · 아이디어(번호 · 치수 · 모델명 · 가격 · 위치 · 사람 이름과 연락처). "
        "단 '~하라고 하심' 처럼 해야 할 일이 생기면 task 로.\n"
        "- done: [이미 있는 것] 중 하나를 끝냈다는 말(했음 · 보냄 · 끝 · 완료 · 주문함 · 넣음). ref 에 그 id. 어느 것인지 확실하지 않으면 "
        "done 으로 하지 않는다. 끝내면서 생긴 다음 일(예: 발주함 → 도착 확인)은 따로 항목으로.\n"
        "- same: [이미 있는 것] 과 같은 일을 다시 적은 것. ref 에 그 id.\n"
        "[title]\n"
        "- 사용자가 쓴 말을 살려 짧게(task 는 40자 이내). 없는 내용을 덧붙이지 않는다. 오타 · 군말 · 날짜 표현만 덜어 낸다. "
        "고유명사 · 숫자 · 사람 이름은 그대로.\n"
        "- note 는 기억할 내용이 빠지지 않게 — 숫자 · 번호 · 치수는 그대로, 100자까지.\n"
        "[day · time · repeat]\n"
        "- 글에 날짜 · 요일 · '다음주 금요일' · '24일' · '3일 뒤' · '내일' 같은 표현이 있을 때만 오늘(%s) 기준으로 계산한다. "
        "지난 날짜가 되면 다음 해당일. 없으면 null. 날짜를 지어내지 않는다. repeat 는 '매주' 가 분명할 때만 weekly.\n"
        "[area · doing · someday]\n"
        "- area: 연구 · 수업 · 토플 · 행정 · 개인 중 하나. [이미 있는 것] 에 비슷한 일이 있으면 같은 area.\n"
        "- doing: 글에 '하는 중' · '지금 하고 있음' 이 있을 때만 true. someday: '언젠가' · '나중에 시간 나면' · '여유 있을 때' 일 때만 true.\n"
        "[needs_lookup]\n"
        "- deadline · event 인데 day 가 null 이고 학회 · 학교 · 공모 · 장학처럼 웹에 공식 일정이 있는 것만 true, "
        "lookup_query 에 연도를 넣은 한국어 검색어.\n"
        "다른 말은 쓰지 말고 JSON 만.\n\n"
        "[글]\n%s" % (today, wd, have, today, text))


def extract_json(out):
    """claude 출력에서 {"items": [...]} 객체를 찾는다(앞뒤 설명 · 코드 울타리가 붙어도). 없으면 None."""
    if not out:
        return None
    s = out.strip()
    dec = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\{", s):
        try:
            obj, _end = dec.raw_decode(s, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            best = obj
    return best


def classify(text, today, ctx):
    if classifier is not None:
        return classifier(text, today, ctx)
    return extract_json(plans.run_claude(prompt(text, today, ctx), CLASSIFY_TIMEOUT))


def normalize(p):
    """claude 의 답을 믿을 수 있는 모양으로. 이상한 값은 버리거나 할 일로 낮춘다."""
    items = []
    for it in ((p or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        typ = it.get("type") if it.get("type") in TYPES else "task"
        title = re.sub(r"\s+", " ", str(it.get("title") or "")).strip()
        ref = str(it.get("ref") or "").strip()
        if typ in ("done", "same") and not _REF_RE.match(ref):
            if not title:
                continue
            typ = "note" if typ == "done" else "task"     # 가리킬 것이 없으면: 끝낸 일은 기록으로, 같은 일은 새 할 일로
        if typ not in ("done", "same") and not title:
            continue
        day = it.get("day") if plans.valid_date(it.get("day")) else None
        tm = it.get("time") if isinstance(it.get("time"), str) and _TIME_RE.match(it.get("time")) else None
        need = bool(it.get("needs_lookup")) and typ in ("deadline", "event") and day is None and bool(it.get("lookup_query"))
        items.append({
            "type": typ, "title": title[:160 if typ == "note" else 100], "day": day, "time": tm,
            "repeat": "weekly" if it.get("repeat") == "weekly" else "",
            "area": str(it.get("area") or "").strip()[:8],
            "doing": it.get("doing") is True and typ == "task", "someday": it.get("someday") is True,
            "ref": ref if typ in ("done", "same") else "",
            "needs_lookup": need, "lookup_query": str(it.get("lookup_query") or "")[:160] if need else "",
        })
    return items


# ── 저장 ────────────────────────────────────────────────────────────
def add(conn, text, day, cfg=None):
    """글을 바로 저장하고 뒤에서 나눈다. claude 가 없으면 예전처럼 할 일 · 일정으로 곧장(§28)."""
    text = "\n".join(re.sub(r"[ \t　]+", " ", ln).strip() for ln in str(text or "").replace("\r", "").split("\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)[:MAX_TEXT]
    if not text:
        raise ValueError("빈 글")
    if not plans.valid_date(day):
        day = common.today_str(cfg)
    now = time.time()
    if not available():
        one = re.sub(r"\s+", " ", text)
        if NEEDS_PARSE.search(one):
            eid, _parsing = plans.add(conn, one, None, cfg)
            return {"id": None, "fallback": "plan", "ref": "p%d" % eid}
        tid = _add_todo(conn, one, day)
        conn.commit()
        return {"id": None, "fallback": "task", "ref": "t%d" % tid}
    cur = conn.execute("INSERT INTO inbox(text, day, status, created_ts, updated_ts) VALUES(?,?,?,?,?)",
                       (text, day, "parsing", now, now))
    conn.commit()
    iid = cur.lastrowid
    if ASYNC:
        threading.Thread(target=process, args=(iid, cfg), daemon=True).start()
    else:
        process(iid, cfg, conn=conn)
    return {"id": iid, "parsing": True}


def process(iid, cfg=None, conn=None):
    own = conn is None
    if own:
        conn = common.connect(cfg)
    try:
        row = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
        if row is None or row["status"] != "parsing":
            return
        today = common.today_str(cfg)
        try:
            items = normalize(classify(row["text"], today, context_lines(conn, today)))
        except Exception:
            items = []
        for eid, title, it in apply(conn, iid, items, today):
            plans.fill_safe(conn, eid, title, {"title": title, "kind": it["type"], "day": None, "time": it["time"],
                                               "repeat": it["repeat"], "needs_lookup": True,
                                               "lookup_query": it["lookup_query"]}, today)
    finally:
        if own:
            conn.close()


def _claim(conn, iid):
    cur = conn.execute("UPDATE inbox SET status='applying', updated_ts=? WHERE id=? AND status='parsing'", (time.time(), iid))
    conn.commit()
    return cur.rowcount == 1


def apply(conn, iid, items, today):
    """나눈 항목을 제자리에 넣는다(한 번에 커밋). 나눈 것이 없거나 넣다가 실패하면 글 전체를 할 일 하나로.
    웹에서 날짜를 찾아야 하는 plans 행은 [(id, 제목, 항목)] 로 돌려준다 — 부르는 쪽이 커밋 뒤에 찾는다."""
    if not _claim(conn, iid):
        return []
    row = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    status, result, lookups = "done", [], []
    try:
        if not items:
            raise ValueError("나눈 것이 없음")
        for it in items:
            r, lk = _apply_one(conn, iid, row["day"], it, today)
            result.append(r)
            if lk:
                lookups.append(lk)
    except Exception:
        conn.rollback()
        status, lookups = "failed", []
        tid = _add_todo(conn, re.sub(r"\s+", " ", row["text"])[:300], row["day"])
        result = [{"act": "add", "ref": "t%d" % tid, "type": "task", "title": re.sub(r"\s+", " ", row["text"])[:300]}]
    conn.execute("UPDATE inbox SET status=?, result=?, updated_ts=? WHERE id=?",
                 (status, json.dumps(result, ensure_ascii=False), time.time(), iid))
    conn.commit()
    return lookups


def _next_sort(conn, day):
    return int(conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM todos WHERE day=?", (day,)).fetchone()[0])


def _add_todo(conn, text, day, doing=False):
    cur = conn.execute("INSERT INTO todos(text, day, done, created_ts, done_ts, sort_order) VALUES(?,?,0,?,NULL,?)",
                       (text, day, time.time(), _next_sort(conn, day)))
    if doing:
        conn.execute("INSERT OR IGNORE INTO todo_progress(todo_id, started_ts) VALUES(?,?)", (cur.lastrowid, time.time()))
    return cur.lastrowid


def _add_keep(conn, text, area=""):
    return conn.execute("INSERT INTO keeps(text, area, created_ts) VALUES(?,?,?)", (text, area or "", time.time())).lastrowid


def current(conn, ref):
    """id 가 가리키는 것 — {type, title, open} 또는 None."""
    k, n = ref[0], int(ref[1:])
    if k == "t":
        r = conn.execute("SELECT * FROM todos WHERE id=?", (n,)).fetchone()
        return {"type": "task", "title": r["text"], "open": not r["done"]} if r else None
    if k == "p":
        r = conn.execute("SELECT * FROM plans WHERE id=?", (n,)).fetchone()
        return {"type": r["kind"] or "task", "title": r["title"] or r["text"], "open": not r["done"]} if r else None
    r = conn.execute("SELECT * FROM keeps WHERE id=?", (n,)).fetchone()
    return {"type": "note", "title": r["text"], "open": True} if r else None


def _apply_one(conn, iid, day, it, today):
    typ, title, now = it["type"], it["title"], time.time()
    if typ in ("done", "same"):
        cur = current(conn, it["ref"])
        if cur is None or not cur["open"]:
            if not title:
                return {"act": "same", "ref": it["ref"], "type": "task", "title": (cur or {}).get("title", "")}, None
            typ = "note" if typ == "done" else "task"
        elif typ == "same" or it["ref"][0] == "k":
            return {"act": "same", "ref": it["ref"], "type": cur["type"], "title": cur["title"]}, None
        else:
            prev = _mark_done(conn, it["ref"], today)
            return {"act": "done", "ref": it["ref"], "type": cur["type"], "title": cur["title"], "prev": prev}, None
    if typ == "note":
        kid = _add_keep(conn, title, it["area"])
        return {"act": "add", "ref": "k%d" % kid, "type": "note", "title": title}, None
    if typ == "task" and not it["day"]:
        tid = _add_todo(conn, title, day, it["doing"])
        set_meta(conn, "t%d" % tid, it["area"], it["someday"], iid)
        return {"act": "add", "ref": "t%d" % tid, "type": "task", "title": title, "doing": bool(it["doing"]),
                "someday": bool(it["someday"])}, None
    need = it["needs_lookup"]
    cur = conn.execute(
        "INSERT INTO plans(text, title, day, time, kind, repeat, confidence, note, status, created_ts, updated_ts) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (title, title[:60], it["day"], it["time"], typ, it["repeat"], "confirmed" if it["day"] else "unknown",
         "날짜를 웹에서 찾는 중" if need else "", "parsing" if need else "ready", now, now))
    eid = cur.lastrowid
    set_meta(conn, "p%d" % eid, it["area"], it["someday"], iid)
    r = {"act": "add", "ref": "p%d" % eid, "type": typ, "title": title[:60], "day": it["day"], "repeat": it["repeat"]}
    return r, ((eid, title[:60], it) if need else None)


def _mark_done(conn, ref, today):
    """끝냄 처리 — 되돌리기를 위해 바꾸기 전 값을 돌려준다."""
    k, n, now = ref[0], int(ref[1:]), time.time()
    if k == "t":
        r = conn.execute("SELECT * FROM todos WHERE id=?", (n,)).fetchone()
        pg = conn.execute("SELECT started_ts FROM todo_progress WHERE todo_id=?", (n,)).fetchone()
        prev = {"day": r["day"], "sort_order": r["sort_order"], "doing_ts": pg["started_ts"] if pg else None}
        # §20: 전에 적은 할 일을 끝내면 '그 날 한 일' 로 — 오늘로 옮긴다
        conn.execute("UPDATE todos SET done=1, done_ts=?, day=? WHERE id=?", (now, max(r["day"], today), n))
        conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (n,))
        return prev
    r = conn.execute("SELECT * FROM plans WHERE id=?", (n,)).fetchone()
    if (r["repeat"] or "") == "weekly" and r["day"]:
        occ = plans.next_occurrence(r["day"], "weekly", today, r["skip_until"]) or today
        nxt = (datetime.datetime.strptime(occ, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        conn.execute("UPDATE plans SET skip_until=?, updated_ts=? WHERE id=?", (nxt, now, n))   # 매주 일정: 이번 것만 끝
        return {"skip_until": r["skip_until"]}
    conn.execute("UPDATE plans SET done=1, done_ts=?, updated_ts=? WHERE id=?", (now, now, n))
    return {"done": 0}


def _delete_ref(conn, ref):
    k, n = ref[0], int(ref[1:])
    if k == "t":
        conn.execute("DELETE FROM todos WHERE id=?", (n,))
        conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (n,))
    elif k == "p":
        conn.execute("DELETE FROM plans WHERE id=?", (n,))
    else:
        conn.execute("DELETE FROM keeps WHERE id=?", (n,))
    drop_meta(conn, ref)


def _restore(conn, ref, prev):
    k, n = ref[0], int(ref[1:])
    if k == "t":
        if conn.execute("SELECT 1 FROM todos WHERE id=?", (n,)).fetchone() is None:
            return
        conn.execute("UPDATE todos SET done=0, done_ts=NULL, day=?, sort_order=? WHERE id=?",
                     (prev.get("day"), prev.get("sort_order") or 0, n))
        if prev.get("doing_ts"):
            conn.execute("INSERT OR IGNORE INTO todo_progress(todo_id, started_ts) VALUES(?,?)", (n, prev["doing_ts"]))
    elif k == "p":
        if "skip_until" in prev:
            conn.execute("UPDATE plans SET skip_until=?, updated_ts=? WHERE id=?", (prev["skip_until"], time.time(), n))
        else:
            conn.execute("UPDATE plans SET done=0, done_ts=NULL, updated_ts=? WHERE id=?", (time.time(), n))


def undo(conn, iid):
    """나눈 것을 모두 되돌리고 원래 글을 돌려준다(입력 칸에 다시 넣어 고쳐 쓰게)."""
    row = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    if row is None:
        raise KeyError(iid)
    if row["status"] not in ("done", "failed"):
        raise ValueError("아직 나누는 중")
    for r in reversed(json.loads(row["result"] or "[]")):
        ref = str(r.get("ref") or "")
        if not _REF_RE.match(ref):
            continue
        if r.get("act") == "add":
            _delete_ref(conn, ref)
        elif r.get("act") == "done":
            _restore(conn, ref, r.get("prev") or {})
    conn.execute("UPDATE inbox SET status='undone', hidden=1, updated_ts=? WHERE id=?", (time.time(), iid))
    conn.commit()
    return row["text"]


def dismiss(conn, iid):
    conn.execute("UPDATE inbox SET hidden=1 WHERE id=?", (iid,))
    conn.commit()


def move(conn, ref, to, day):
    """잘못 나뉜 것을 옮긴다: 할 일 · 일정 → 기억, 기억 → 할 일(날짜 없음). 새 id 를 돌려준다."""
    if not _REF_RE.match(str(ref or "")) or to not in ("note", "task"):
        raise ValueError("ref · to 가 이상합니다")
    cur = current(conn, ref)
    if cur is None:
        raise KeyError(ref)
    area = (meta_map(conn).get(ref) or {}).get("area", "")
    if ref[0] == "k":
        if to != "task":
            raise ValueError("이미 기억입니다")
        r = conn.execute("SELECT area FROM keeps WHERE id=?", (int(ref[1:]),)).fetchone()
        tid = _add_todo(conn, cur["title"], day if plans.valid_date(day) else common.today_str())
        set_meta(conn, "t%d" % tid, (r["area"] if r else "") or area)
        new = "t%d" % tid
    else:
        if to != "note":
            raise ValueError("이미 할 일입니다")
        new = "k%d" % _add_keep(conn, cur["title"], area)
    _delete_ref(conn, ref)
    conn.commit()
    return new


def _rescue_stale(conn, now):
    """나누던 중 서버가 꺼져 오래 멈춘 글을 할 일 하나로 살린다(적은 글은 잃지 않는다)."""
    conn.execute("UPDATE inbox SET status='parsing' WHERE status='applying' AND updated_ts < ?", (now - STALE_SEC,))
    conn.commit()
    for r in conn.execute("SELECT id FROM inbox WHERE status='parsing' AND updated_ts < ?", (now - STALE_SEC,)).fetchall():
        apply(conn, r["id"], [], common.today_str())


TYPE_NAME = {"task": "할 일", "deadline": "마감", "event": "일정", "note": "기억"}


def _label(r):
    if r.get("act") == "done":
        return "끝냄"
    if r.get("act") == "same":
        return "이미 있음"
    name = TYPE_NAME.get(r.get("type"), "할 일")
    if r.get("doing"):
        name = "하는 중"
    elif r.get("someday") and r.get("type") == "task" and not r.get("day"):
        name = "나중에"
    if r.get("day"):
        name += " " + _md(r["day"])
    elif r.get("type") in ("deadline", "event"):
        name += " · 날짜 미정"
    return name


def recent(conn, now=None):
    """화면에 띄울 것: 나누는 중인 글 + 10분 안에 나눈 글(확인을 누르지 않은 것)."""
    now = now or time.time()
    _rescue_stale(conn, now)
    out = []
    for r in conn.execute("SELECT * FROM inbox WHERE hidden=0 AND (status IN ('parsing','applying') OR updated_ts >= ?) "
                          "ORDER BY id", (now - SHOW_SEC,)).fetchall():
        res = json.loads(r["result"] or "[]")
        for x in res:
            x.pop("prev", None)
            x["label"] = _label(x)
        out.append({"id": r["id"], "text": r["text"], "status": "parsing" if r["status"] == "applying" else r["status"],
                    "result": res, "created_ts": r["created_ts"]})
    return out


# ── 기억 (keeps) ────────────────────────────────────────────────────
def keeps(conn):
    return [{"id": r["id"], "text": r["text"], "area": r["area"] or "", "created_ts": r["created_ts"]}
            for r in conn.execute("SELECT * FROM keeps ORDER BY id DESC")]


def keep_add(conn, text, area=""):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("빈 글")
    kid = _add_keep(conn, text[:500], area)
    conn.commit()
    return kid


def keep_update(conn, kid, text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("빈 글")
    conn.execute("UPDATE keeps SET text=? WHERE id=?", (text[:500], kid))
    conn.commit()


def keep_delete(conn, kid):
    conn.execute("DELETE FROM keeps WHERE id=?", (kid,))
    drop_meta(conn, "k%d" % kid)
    conn.commit()
