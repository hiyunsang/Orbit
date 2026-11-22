# -*- coding: utf-8 -*-
"""
inbox.py — 받아 적기 · 개인 비서 (DESIGN §51).

떠오르는 대로 적은 글(한 줄이든, 쉼표·줄바꿈으로 이어진 여러 생각이든)을 받자마자 inbox 표에 저장하고
(status=parsing) 뒤 스레드가 claude -p 로 나눠 제자리에 넣는다.
  - 날짜 없는 할 일 → todos(§20)          - 날짜 있는 할 일 · 마감 · 일정 → plans(§28)
  - 기억할 것(들은 말 · 번호 · 치수 · 아이디어) → keeps
  - 이미 있는 것을 끝냈다는 말 → 그 항목을 끝냄      - 같은 것을 또 적음 → 그대로 둔다
  - 이미 있는 것에 새 정보(날짜 · 기한 · 하는 중 · 세부)가 붙음 → 그 줄을 고친다(§52, 할 일의 기한은 todo_due)
claude 는 나눈 것과 함께 비서로서 한 말(reply — 무엇을 고쳤는지, 빠진 것이 있으면 질문 하나)을 돌려준다.
최근 하루 동안 적은 글과 그때 한 말을 같이 주어 대화가 이어진다(§52.4).
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

TYPES = ("task", "deadline", "event", "note", "done", "same", "update")
AREAS = ("연구", "수업", "토플", "행정", "개인")
CLASSIFY_TIMEOUT = 150
STALE_SEC = 5 * 60          # 이보다 오래 parsing 이면(서버가 꺼졌다 켜짐 등) 글을 할 일 하나로 살린다
SHOW_SEC = 24 * 3600        # §52 나눈 결과 · 비서의 말은 확인을 누를 때까지(하루 지나면 저절로 내려감)
HIST_SEC = 24 * 3600        # §52.4 claude 에게 같이 줄 '최근에 적은 글과 그때 한 말' 의 범위
HIST_N = 4
REPLY_MAX = 400
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


# ── 할 일의 기한 (todo_due, §52.2) ───────────────────────────────────
def due_map(conn):
    return dict((r["todo_id"], r["due"]) for r in conn.execute("SELECT todo_id, due FROM todo_due"))


def set_due(conn, tid, due):
    """기한을 두거나(YYYY-MM-DD) 뺀다(None)."""
    if due and plans.valid_date(due):
        conn.execute("INSERT OR REPLACE INTO todo_due(todo_id, due) VALUES(?,?)", (tid, due))
    else:
        conn.execute("DELETE FROM todo_due WHERE todo_id=?", (tid,))


def _late(day, today):
    """밀린 날 수(지났으면 양수)."""
    a = datetime.datetime.strptime(day, "%Y-%m-%d")
    b = datetime.datetime.strptime(today, "%Y-%m-%d")
    return (b - a).days


# ── claude 에게 줄 것 ──────────────────────────────────────────────
def context_lines(conn, today, limit=CTX_MAX):
    """끝내지 않은 것 목록 — id 와 함께. claude 가 '끝냈다' · '이미 있다' 를 가리킬 때 이 id 를 쓴다."""
    meta = meta_map(conn)
    dues = due_map(conn)
    out = []
    for r in conn.execute(
            "SELECT t.id, t.text, t.day, (p.todo_id IS NOT NULL) AS doing FROM todos t "
            "LEFT JOIN todo_progress p ON p.todo_id=t.id WHERE t.done=0 ORDER BY doing DESC, t.id DESC"):
        ref = "t%d" % r["id"]
        bits = [r["text"], "할 일"]
        if r["doing"]:
            bits.append("하는 중")
        due = dues.get(r["id"])
        if due:
            late = _late(due, today)
            bits.append("%s까지%s" % (_md(due), (" · %d일 지남" % late) if late > 0 else (" · 오늘" if late == 0 else "")))
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
        elif d.get("occ") and d.get("d") is not None and d["d"] <= 0:
            bits.append("%d일 지남" % -d["d"] if d["d"] < 0 else "오늘")       # §52.4 비서가 밀린 것을 알아야 짚는다
        m = meta.get(ref)
        if m and m["area"]:
            bits.append(m["area"])
        out.append("%s %s" % (ref, " · ".join(bits)))
    for r in conn.execute("SELECT id, text, area FROM keeps ORDER BY id DESC LIMIT 40"):
        out.append("k%d %s · 기억%s" % (r["id"], r["text"], (" · " + r["area"]) if r["area"] else ""))
    return out[:limit]


def history(conn, iid, now=None, limit=HIST_N):
    """§52.4 최근 하루 동안 적은 글과 그때 한 말(오래된 것부터) — [(글, 한 말)]."""
    now = now or time.time()
    rows = conn.execute("SELECT * FROM inbox WHERE id<>? AND status IN ('done','failed') AND created_ts >= ? "
                        "ORDER BY id DESC LIMIT ?", (iid, now - HIST_SEC, limit)).fetchall()
    return [(r["text"], parse_result(r["result"])[1]) for r in reversed(rows)]


def prompt(text, today, ctx, hist=None, clock=None, target=None):
    wd = plans._wd(today)
    # §57.5 '내일' 단추로 적은 글 — 날짜 없는 할 일은 그대로 그 날 목록으로 가고, 날짜 없는 일정 · 마감만 그 날로
    aim = ("[이 글은 사용자가 %s 적었다 — %s(%s요일) 할 것] 날짜 표현이 없는 task 는 day 를 null 로 둔다(그대로 그 날 "
           "할 일 목록에 들어간다). 날짜 표현이 없는 event · deadline 은 day = %s.\n\n"
           % ("'내일' 단추를 누르고" if target == (datetime.datetime.strptime(today, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
              else "날짜 칸에서 그 날을 고르고", target, plans._wd(target), target)) if target and target > today else ""
    clock = clock or time.strftime("%H:%M")                     # §57 밤에 적는 할 일은 대개 다음 출근 날 것
    have = "\n".join(ctx) if ctx else "(없음)"
    talk = "\n".join("- 적은 글: %s\n  네가 한 말: %s" % (re.sub(r"\s+", " ", t)[:400], (r or "(없음)")) for t, r in (hist or []))
    return (
        "오늘은 %s(%s요일), 지금 %s 이다. 너는 한국 대학원생(기계공학 석사, 연구실에서 장비 설계·발주·실험을 하고 수업과 토플 학원도 다님)의 "
        "개인 비서다. 이 사람은 머릿속에 떠오르는 할 일 · 일정 · 기억할 것을 아래 [글]처럼 아무렇게나 던져 둔다. "
        "그것을 나눠 정리하고 JSON 하나로만 답하라.\n\n%s"
        "[이미 있는 것] — 아직 안 끝낸 것. 맨 앞이 id (t=할 일, p=날짜 있는 할 일·마감·일정, k=기억). "
        "'n일 지남' 은 그 날짜가 지나도록 안 끝낸 것\n%s\n\n"
        "[최근에 적은 글과 네가 한 말] — 오래된 것부터. 이번 글이 네 질문에 대한 답일 수 있다\n%s\n\n"
        "[출력] {\"items\": [{\"type\": \"task|deadline|event|note|done|same|update\", \"title\": \"…\", "
        "\"day\": \"YYYY-MM-DD 또는 null\", \"time\": \"HH:MM 또는 null\", \"repeat\": \"none|weekly\", "
        "\"area\": \"연구|수업|토플|행정|개인\", \"doing\": false, \"someday\": false, \"ref\": \"id 또는 null\", "
        "\"needs_lookup\": false, \"lookup_query\": \"\"}], \"reply\": \"비서로서 하는 말\"}\n\n"
        "[나누는 법]\n"
        "- 한 가지 일 = 한 항목. 쉼표 · 줄바꿈 · '그리고' 로 서로 다른 일이 이어지면 나눈다. 한 일의 세부(누구에게 · 무엇을 · 몇 개 · 왜)는 "
        "나누지 말고 그 항목 title 에 남긴다.\n"
        "- task: 해야 할 행동(메일 · 연락 · 발주 · 구매 · 설계 · 수정 · 예약 · 준비 등). 날짜 표현이 있으면 day 를 채운다.\n"
        "- task 는 날짜가 없어도 된다 — 날짜 없는 할 일이 곧 이 사람의 할 일 목록(오늘 · 다음 출근 날 할 것)이다. 밤에 적는 할 일은 "
        "대개 다음 날 출근해서 할 일이니 그대로 날짜 없이 둔다.\n"
        "- deadline: 제출 · 신청 · 접수 · 납부처럼 기한이 핵심인 것.\n"
        "- event: 수업 · 회의 · 미팅 · 약속 · 시험처럼 그 날 그 시각에 있는 일.\n"
        "- note: 행동이 아니라 기억해 둘 사실 · 정보 · 들은 말 · 아이디어(번호 · 치수 · 모델명 · 가격 · 위치 · 사람 이름과 연락처). "
        "단 '~하라고 하심' 처럼 해야 할 일이 생기면 task 로.\n"
        "- done: [이미 있는 것] 중 하나를 끝냈다는 말(했음 · 보냄 · 끝 · 완료 · 주문함 · 넣음). ref 에 그 id. 어느 것인지 확실하지 않으면 "
        "done 으로 하지 않는다. 끝내면서 생긴 다음 일(예: 발주함 → 도착 확인)은 따로 항목으로.\n"
        "- update: [이미 있는 것] 과 같은 일인데 새 정보가 붙은 것 — 날짜 · 기한('내일까진' · '금요일까지'), 시각, '하는 중', "
        "더해진 세부, 빠졌던 숫자 · 날짜. ref 에 그 id, 바뀐 것만 채운다(day = 새 날짜 또는 기한, title 은 세부가 더해질 때만 원래 제목에 "
        "덧붙여서, 아니면 null). 지난 날짜로 밀려 있던 일에 새 기한을 말하면 반드시 update.\n"
        "- same: [이미 있는 것] 과 같은 일을 새 정보 없이 다시 적은 것. ref 에 그 id.\n"
        "- 묶어 말하기: '발주 넣을 거 다' 처럼 [이미 있는 것] 여러 줄을 한꺼번에 가리키면 해당하는 줄마다 update(또는 same). "
        "해당하는 줄이 하나도 없을 때만 새 task 하나.\n"
        "[title]\n"
        "- 사용자가 쓴 말을 살려 짧게(task 는 40자 이내). 없는 내용을 덧붙이지 않는다. 오타 · 군말 · 날짜 표현만 덜어 낸다. "
        "고유명사 · 숫자 · 사람 이름은 그대로.\n"
        "- note 는 기억할 내용이 빠지지 않게 — 숫자 · 번호 · 치수는 그대로, 100자까지.\n"
        "[day · time · repeat]\n"
        "- 글에 날짜 · 요일 · '다음주 금요일' · '24일' · '3일 뒤' · '내일' 같은 표현이 있을 때만 오늘(%s) 기준으로 계산한다. "
        "지난 날짜가 되면 다음 해당일. 없으면 null. 날짜를 지어내지 않는다. repeat 는 '매주' 가 분명할 때만 weekly.\n"
        "- '내일까지 해야 함' · '낼 끝내자' 처럼 기한을 말하면 그 날이 day. 문장 끝에서 묶는 말('낼 끝을 내자' · '이번 주 안에')은 "
        "그 문장의 일 모두에, 글 끝에 있으면 글 전체의 일 모두에 붙인다.\n"
        "[area · doing · someday]\n"
        "- area: 연구 · 수업 · 토플 · 행정 · 개인 중 하나. [이미 있는 것] 에 비슷한 일이 있으면 같은 area.\n"
        "- doing: 글에 '하는 중' · '지금 하고 있음' 이 있을 때만 true. someday: '언젠가' · '나중에 시간 나면' · '여유 있을 때' 일 때만 true.\n"
        "[needs_lookup]\n"
        "- deadline · event 인데 day 가 null 이고 학회 · 학교 · 공모 · 장학처럼 웹에 공식 일정이 있는 것만 true, "
        "lookup_query 에 연도를 넣은 한국어 검색어.\n"
        "[reply — 비서로서 하는 말]\n"
        "- 존댓말('~요') 1~3문장, 200자 이내. 고친 것(특히 밀려 있던 것을 찾아 날짜를 바꾼 것) · 겹치는 것 · 급한 것을 짚는다. "
        "나눈 목록을 그대로 되풀이하지 않는다. 없는 사실을 지어내지 않는다.\n"
        "- 묻는 것은 마지막 수단: 기억할 사실의 핵심(날짜 · 숫자 · 누구)이 빠져 그대로는 쓸모가 없을 때('발표날 나옴' 처럼)만 "
        "질문 하나로 끝낸다. 그런 것은 note 로 만들지 말고 묻는다. 답이 오면 해당 줄을 update.\n"
        "- task 의 기한 · 날짜 · 순서는 절대 묻지 않는다(기한은 사용자가 말할 때만 붙인다). deadline · event 의 날짜가 빠진 것도 "
        "묻지 않는다 — 화면의 '날짜 확인' 칸에서 사용자가 고른다.\n"
        "- 하는 법을 가르치는 말('메일을 먼저 보내고 …')은 하지 않는다. 짚을 것이 없으면 무엇을 어디에 넣었는지 한 문장.\n"
        "다른 말은 쓰지 말고 JSON 만.\n\n"
        "[글]\n%s" % (today, wd, clock, aim, have, talk or "(없음)", today, text))


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


def classify(text, today, ctx, hist=None, target=None):
    if classifier is not None:
        return classifier(text, today, ctx, hist)
    return extract_json(plans.run_claude(prompt(text, today, ctx, hist, target=target), CLASSIFY_TIMEOUT))


def reply_of(p):
    """claude 가 비서로서 한 말 — 없거나 이상하면 빈 글."""
    r = (p or {}).get("reply") if isinstance(p, dict) else None
    return re.sub(r"\s+", " ", r).strip()[:REPLY_MAX] if isinstance(r, str) else ""


def parse_result(raw):
    """inbox.result → (항목 목록, 한 말). §51 의 목록 모양 행도 읽는다."""
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        return [], ""
    if isinstance(v, dict):
        return list(v.get("items") or []), str(v.get("reply") or "")
    return (v if isinstance(v, list) else []), ""


def normalize(p):
    """claude 의 답을 믿을 수 있는 모양으로. 이상한 값은 버리거나 할 일로 낮춘다."""
    items = []
    for it in ((p or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        typ = it.get("type") if it.get("type") in TYPES else "task"
        title = re.sub(r"\s+", " ", str(it.get("title") or "")).strip()
        ref = str(it.get("ref") or "").strip()
        if typ in ("done", "same", "update") and not _REF_RE.match(ref):
            if not title:
                continue
            typ = "note" if typ == "done" else "task"     # 가리킬 것이 없으면: 끝낸 일은 기록으로, 같은 · 고칠 일은 새 할 일로
        if typ not in ("done", "same", "update") and not title:
            continue
        day = it.get("day") if plans.valid_date(it.get("day")) else None
        tm = it.get("time") if isinstance(it.get("time"), str) and _TIME_RE.match(it.get("time")) else None
        need = bool(it.get("needs_lookup")) and typ in ("deadline", "event") and day is None and bool(it.get("lookup_query"))
        items.append({
            "type": typ, "title": title[:160 if typ == "note" else 100], "day": day, "time": tm,
            "repeat": "weekly" if it.get("repeat") == "weekly" else "",
            "area": str(it.get("area") or "").strip()[:8],
            "doing": it.get("doing") is True and typ in ("task", "update"), "someday": it.get("someday") is True,
            "ref": ref if typ in ("done", "same", "update") else "",
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
        today = common.today_str(cfg, now=row["created_ts"])        # §52.4 적은 때의 '오늘'(다시 나눠도 그때의 '내일')
        try:
            p = classify(row["text"], today, context_lines(conn, today), history(conn, iid),
                         target=row["day"] if row["day"] and row["day"] > today else None)
            items, reply = normalize(p), reply_of(p)
        except Exception:
            items, reply = [], ""
        for eid, title, it in apply(conn, iid, items, today, reply):
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


def apply(conn, iid, items, today, reply=""):
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
        status, lookups, reply = "failed", [], ""
        tid = _add_todo(conn, re.sub(r"\s+", " ", row["text"])[:300], row["day"])
        result = [{"act": "add", "ref": "t%d" % tid, "type": "task", "title": re.sub(r"\s+", " ", row["text"])[:300]}]
    conn.execute("UPDATE inbox SET status=?, result=?, updated_ts=? WHERE id=?",
                 (status, json.dumps({"items": result, "reply": reply or ""}, ensure_ascii=False), time.time(), iid))
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
    if typ in ("done", "same", "update"):
        cur = current(conn, it["ref"])
        if cur is None or not cur["open"]:
            if not title:
                return {"act": "same", "ref": it["ref"], "type": "task", "title": (cur or {}).get("title", "")}, None
            typ = "note" if typ == "done" else "task"
        elif typ == "update":
            return _update(conn, it, cur, today), None
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


def _update(conn, it, cur, today):
    """§52.1 이미 있는 줄을 고친다 — 바뀐 것만. 고치기 전 값(prev)을 돌려줘 되돌리기가 쓴다."""
    ref = it["ref"]
    k, n, now = ref[0], int(ref[1:]), time.time()
    title = it["title"]
    out = {"act": "update", "ref": ref, "type": cur["type"], "title": cur["title"]}
    if k == "p":
        r = conn.execute("SELECT * FROM plans WHERE id=?", (n,)).fetchone()
        prev = dict((c, r[c]) for c in ("day", "time", "title", "confidence", "status", "note"))
        sets, params = [], []
        if it["day"] and (r["repeat"] or "") != "weekly" and it["day"] != r["day"]:     # 매주 일정의 기준일은 고치지 않는다
            sets += ["day=?", "confidence=?"]
            params += [it["day"], "confirmed"]
            if r["status"] in ("parsing", "failed"):
                sets += ["status=?", "note=?"]
                params += ["ready", ""]
            out["day"] = it["day"]
        if it["time"] and it["time"] != r["time"]:
            sets.append("time=?")
            params.append(it["time"])
            out["time"] = it["time"]
        if title and title[:60] != (r["title"] or r["text"]):
            sets.append("title=?")
            params.append(title[:60])
            out["title"], out["retitled"] = title[:60], True
        if sets:
            conn.execute("UPDATE plans SET %s, updated_ts=? WHERE id=?" % ", ".join(sets), params + [now, n])
    elif k == "t":
        r = conn.execute("SELECT * FROM todos WHERE id=?", (n,)).fetchone()
        pg = conn.execute("SELECT started_ts FROM todo_progress WHERE todo_id=?", (n,)).fetchone()
        due0 = due_map(conn).get(n)
        sd0 = (meta_map(conn).get(ref) or {}).get("someday", 0)
        prev = {"text": r["text"], "due": due0, "doing_ts": pg["started_ts"] if pg else None, "someday": sd0}
        if it["day"] and it["day"] != due0:
            set_due(conn, n, it["day"])
            out["day"], out["due"] = it["day"], True
            if sd0:
                set_meta(conn, ref, someday=False)          # 기한이 생기면 '나중에' 가 아니다
        if it["doing"] and not pg:
            conn.execute("INSERT OR IGNORE INTO todo_progress(todo_id, started_ts) VALUES(?,?)", (n, now))
            out["doing"] = True
            if sd0:
                set_meta(conn, ref, someday=False)
        if it["someday"] and not sd0 and not out.get("due") and not out.get("doing"):
            set_meta(conn, ref, someday=True)
            out["someday"] = True
        if title and title != r["text"]:
            conn.execute("UPDATE todos SET text=? WHERE id=?", (title, n))
            out["title"], out["retitled"] = title, True
    else:
        r = conn.execute("SELECT * FROM keeps WHERE id=?", (n,)).fetchone()
        prev = {"text": r["text"]}
        if title and title != r["text"]:
            conn.execute("UPDATE keeps SET text=? WHERE id=?", (title, n))
            out["title"], out["retitled"] = title, True
    if not any(out.get(x) for x in ("day", "time", "doing", "someday", "retitled")):
        return {"act": "same", "ref": ref, "type": cur["type"], "title": cur["title"]}      # 바뀐 것이 없으면 '이미 있음'
    out["prev"] = prev
    return out


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
        conn.execute("DELETE FROM todo_due WHERE todo_id=?", (n,))
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


def _restore_update(conn, ref, prev):
    k, n = ref[0], int(ref[1:])
    if k == "p":
        if conn.execute("SELECT 1 FROM plans WHERE id=?", (n,)).fetchone() is None:
            return
        conn.execute("UPDATE plans SET day=?, time=?, title=?, confidence=?, status=?, note=?, updated_ts=? WHERE id=?",
                     (prev.get("day"), prev.get("time"), prev.get("title"), prev.get("confidence") or "unknown",
                      prev.get("status") or "ready", prev.get("note") or "", time.time(), n))
    elif k == "t":
        if conn.execute("SELECT 1 FROM todos WHERE id=?", (n,)).fetchone() is None:
            return
        if prev.get("text"):
            conn.execute("UPDATE todos SET text=? WHERE id=?", (prev["text"], n))
        set_due(conn, n, prev.get("due"))
        if prev.get("doing_ts"):
            conn.execute("INSERT OR IGNORE INTO todo_progress(todo_id, started_ts) VALUES(?,?)", (n, prev["doing_ts"]))
        else:
            conn.execute("DELETE FROM todo_progress WHERE todo_id=?", (n,))
        set_meta(conn, ref, someday=bool(prev.get("someday")))
    elif prev.get("text"):
        conn.execute("UPDATE keeps SET text=? WHERE id=?", (prev["text"], n))


def undo(conn, iid):
    """나눈 것을 모두 되돌리고 원래 글을 돌려준다(입력 칸에 다시 넣어 고쳐 쓰게)."""
    row = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    if row is None:
        raise KeyError(iid)
    if row["status"] not in ("done", "failed"):
        raise ValueError("아직 나누는 중")
    for r in reversed(parse_result(row["result"])[0]):
        ref = str(r.get("ref") or "")
        if not _REF_RE.match(ref):
            continue
        if r.get("act") == "add":
            _delete_ref(conn, ref)
        elif r.get("act") == "done":
            _restore(conn, ref, r.get("prev") or {})
        elif r.get("act") == "update":
            _restore_update(conn, ref, r.get("prev") or {})
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
    if r.get("act") == "update":
        bits = []
        if r.get("day"):
            bits.append(_md(r["day"]) + ("까지" if r.get("due") else ""))
        if r.get("time"):
            bits.append(r["time"])
        if r.get("doing"):
            bits.append("하는 중")
        if r.get("someday"):
            bits.append("나중에")
        return " · ".join(["고침"] + bits)
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
    """화면에 띄울 것: 나누는 중인 글 + 확인을 누르지 않은 나눈 글(하루까지 — §52.3)."""
    now = now or time.time()
    _rescue_stale(conn, now)
    out = []
    for r in conn.execute("SELECT * FROM inbox WHERE hidden=0 AND (status IN ('parsing','applying') OR updated_ts >= ?) "
                          "ORDER BY id", (now - SHOW_SEC,)).fetchall():
        res, reply = parse_result(r["result"])
        for x in res:
            x.pop("prev", None)
            x["label"] = _label(x)
        out.append({"id": r["id"], "text": r["text"], "status": "parsing" if r["status"] == "applying" else r["status"],
                    "result": res, "reply": reply, "created_ts": r["created_ts"]})
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
