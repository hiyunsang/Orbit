# -*- coding: utf-8 -*-
"""
anki_stats.py — Anki 복습 모니터 (DESIGN §31)

Anki 데스크톱의 컬렉션(`%APPDATA%\\Anki2\\<프로필>\\collection.anki2`, SQLite)을 **사본을 떠서 읽기 전용**으로 연다.
Anki 가 열려 있어도 되고(잠금을 안 건드림), 쓰지 않는다. 폰에서 한 복습은 AnkiWeb 동기화로 이 파일에 들어온다.

**Anki 데스크톱(26.9 실측)은 컬렉션을 프로필을 열 때·닫을 때만 동기화한다.** 주기 타이머(`on_periodic_sync_timer`)는
미디어만 동기화한다. 그래서 켜 둔 채로 두면 폰 기록이 안 내려온다. 이 모듈은 AnkiConnect(또는 같은 프로토콜의
호환 애드온)가 127.0.0.1:8765 에 떠 있으면 `SYNC_EVERY_SEC` 마다 `sync` 를 대신 눌러 준다(`maybe_sync`). 없으면
카드가 "Anki 창에서 Y" 를 안내한다.

표준 라이브러리만. 계산은 순수 함수(`compute`)라 가짜 컬렉션으로 테스트한다. `maybe_sync` 는 `post` 를 주입해 테스트한다.
"""
import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.request

ROLLOVER_HOUR = 4          # Anki 의 하루 시작(기본 4시). col.conf 의 rollover 가 있으면 그걸 쓴다
CACHE_SEC = 10 * 60        # 파일이 안 바뀌어도 이만큼은 다시 읽지 않는다
CONNECT_URL = "http://127.0.0.1:8765"   # AnkiConnect 기본 주소
SYNC_EVERY_SEC = 30 * 60   # AnkiConnect 로 sync 를 누르는 간격
SYNC_TIMEOUT = 120         # sync 응답 대기(초). AnkiWeb 이 느리면 길어진다
_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "at": 0, "data": None}
# AnkiConnect 상태 — present: 마지막으로 물었을 때 답했는가(None=아직 모름), busy: sync 진행 중
_connect = {"present": None, "busy": False, "last_try": 0, "last_ok": 0, "error": None}
_connect_lock = threading.Lock()
DAY_TAG = re.compile(r"(?:^|\s)Day\s?0*(\d{1,3})(?=\s|$)", re.I)    # §59 노트 태그의 Day01 · Day 12 — 단어장의 '몇째 날'
PROGRESS_MAX = 5

try:
    import anki_mirror          # §59 Orbit 이 직접 받아 오는 사본
except Exception:               # noqa: BLE001 — 없어도 §31 의 길(이 PC 의 Anki 파일)은 그대로
    anki_mirror = None


def find_collection(appdata=None):
    """가장 최근에 바뀐 프로필의 collection.anki2 경로. 없으면 None."""
    base = os.path.join(appdata or os.environ.get("APPDATA", ""), "Anki2")
    cols = glob.glob(os.path.join(base, "*", "collection.anki2"))
    return max(cols, key=os.path.getmtime) if cols else None


def _day_start(now, rollover):
    lt = time.localtime(now)
    d0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, rollover, 0, 0, 0, 0, -1))
    if now < d0:
        d0 -= 86400
    return d0


def _deck_names(con):
    names = {}
    try:
        for r in con.execute("SELECT id, name FROM decks"):
            names[int(r[0])] = str(r[1]).replace("\x1f", "::")
    except sqlite3.Error:
        pass
    if not names:   # 옛 컬렉션: col.decks JSON
        try:
            raw = con.execute("SELECT decks FROM col").fetchone()[0]
            for k, v in json.loads(raw).items():
                names[int(k)] = v.get("name", str(k))
        except Exception:
            pass
    return names


def compute(con, now=None):
    """열린 연결(사본)에서 숫자를 뽑는다. 순수 계산."""
    now = now or time.time()
    rollover = ROLLOVER_HOUR
    try:
        conf = json.loads(con.execute("SELECT conf FROM col").fetchone()[0] or "{}")
        if isinstance(conf.get("rollover"), int) and 0 <= conf["rollover"] <= 23:
            rollover = conf["rollover"]
    except Exception:
        pass
    crt = con.execute("SELECT crt FROM col").fetchone()[0]
    day0 = _day_start(now, rollover)
    today_idx = int((now - crt) // 86400)

    def rv(sql, args=()):
        return con.execute(sql, args).fetchone()

    r = rv("SELECT count(*), coalesce(sum(time),0), coalesce(sum(case when ease>1 then 1 else 0 end),0) "
           "FROM revlog WHERE id >= ? AND type IN (0,1,2)", (int(day0 * 1000),))
    today = {"reviews": int(r[0]), "minutes": round(r[1] / 60000.0, 1), "correct": int(r[2])}
    today["accuracy"] = round(100.0 * today["correct"] / today["reviews"]) if today["reviews"] else None

    # 남은 것: 복습 큐(2)·하루 학습(3)은 날짜 번호, 학습 중(1)은 초
    due_review = rv("SELECT count(*) FROM cards WHERE queue IN (2,3) AND due <= ?", (today_idx,))[0]
    due_learn = rv("SELECT count(*) FROM cards WHERE queue = 1 AND due <= ?", (int(now),))[0]
    new_waiting = rv("SELECT count(*) FROM cards WHERE queue = 0")[0]

    # 최근 N일 날짜별 (오늘 포함, 오래된 날부터)
    days = []
    for i in range(29, -1, -1):
        a = day0 - i * 86400
        c = rv("SELECT count(*) FROM revlog WHERE id >= ? AND id < ? AND type IN (0,1,2)", (int(a * 1000), int((a + 86400) * 1000)))[0]
        days.append({"day": time.strftime("%Y-%m-%d", time.localtime(a)), "n": int(c)})
    # 연속 일수: 오늘 했으면 오늘부터, 아니면 어제부터 거꾸로
    streak = 0
    seq = [d["n"] for d in days]
    start = len(seq) - 1 if seq[-1] > 0 else len(seq) - 2
    for i in range(start, -1, -1):
        if seq[i] > 0:
            streak += 1
        else:
            break
    if streak == 30 and start == 29:
        # 30일 넘게 이어진 경우 더 센다
        extra = rv("SELECT count(DISTINCT cast((id/1000 - ?) / 86400 as int)) FROM revlog WHERE id < ? AND type IN (0,1,2)",
                   (rollover * 3600, int((day0 - 29 * 86400) * 1000)))[0]
        streak += int(extra)   # 근사 — 끊김 없이 이어졌다고 본다(30일 이상이면 정확도보다 격려가 목적)

    # 30일 정답률(복습 type=1 만, 학습 단계는 제외)
    r30 = rv("SELECT count(*), coalesce(sum(case when ease>1 then 1 else 0 end),0) FROM revlog WHERE id >= ? AND type = 1",
             (int((day0 - 29 * 86400) * 1000),))
    retention30 = round(100.0 * r30[1] / r30[0]) if r30[0] else None

    # 오늘 덱별
    names = _deck_names(con)
    by_deck = {}
    for cid_did, n in con.execute(
            "SELECT c.did, count(*) FROM revlog r JOIN cards c ON c.id = r.cid WHERE r.id >= ? AND r.type IN (0,1,2) GROUP BY c.did",
            (int(day0 * 1000),)):
        top = names.get(int(cid_did), str(cid_did)).split("::")[0]
        by_deck[top] = by_deck.get(top, 0) + int(n)
    decks = sorted(by_deck.items(), key=lambda x: -x[1])

    progress = _progress(con, names, day0, by_deck)
    last = rv("SELECT max(id) FROM revlog")[0]
    total_cards = rv("SELECT count(*) FROM cards")[0]
    mature = rv("SELECT count(*) FROM cards WHERE queue = 2 AND ivl >= 21")[0]
    # col.ls = 마지막으로 '바뀐 것을 주고받은' 동기화 시각(ms). 바뀐 게 없던 동기화는 갱신하지 않는다
    try:
        ls = rv("SELECT ls FROM col")[0]
    except sqlite3.Error:
        ls = None
    return {
        "today": today,
        "due": {"review": int(due_review), "learn": int(due_learn), "new_waiting": int(new_waiting)},
        "days": days[-7:], "days30": days, "streak": int(streak), "retention30": retention30,
        "decks": [{"name": k, "n": v} for k, v in decks[:6]],
        "progress": progress,
        "last_review": (last / 1000.0) if last else None,
        "last_sync": (ls / 1000.0) if ls else None,
        "cards": {"total": int(total_cards), "mature": int(mature)},
    }


def _progress(con, names, day0, by_deck):
    """§59 덱 진행 — 맨 위 덱마다 전체 · 본 것(새 카드가 아닌 것) · 오늘 처음 본 것 · 오늘 복습.
    노트 태그에 DayNN 이 있으면 지금 Day(본 카드 가운데 가장 큰 번호) / 마지막 Day. 아직 새 카드가 남았거나 한 달 안에 본 덱만,
    오늘 한 것 → 최근에 본 것 → 큰 것 순으로 PROGRESS_MAX 개."""
    top = lambda did: names.get(int(did), str(did)).split("::")[0]
    prog = {}
    try:
        for did, n, seen in con.execute("SELECT did, count(*), sum(case when type != 0 then 1 else 0 end) FROM cards GROUP BY did"):
            p = prog.setdefault(top(did), {"total": 0, "learned": 0, "new_today": 0, "day": None, "days": None, "last": 0})
            p["total"] += int(n)
            p["learned"] += int(seen or 0)
        for did, n in con.execute("SELECT c.did, count(*) FROM (SELECT cid, min(id) AS mid FROM revlog GROUP BY cid) f "
                                  "JOIN cards c ON c.id = f.cid WHERE f.mid >= ? GROUP BY c.did", (int(day0 * 1000),)):
            if top(did) in prog:
                prog[top(did)]["new_today"] += int(n)
        for did, last in con.execute("SELECT c.did, max(r.id) FROM revlog r JOIN cards c ON c.id = r.cid GROUP BY c.did"):
            if top(did) in prog and last:
                prog[top(did)]["last"] = max(prog[top(did)]["last"], last / 1000.0)
    except sqlite3.Error:
        return []
    try:
        for did, typ, tags in con.execute("SELECT c.did, c.type, n.tags FROM cards c JOIN notes n ON n.id = c.nid"):
            m = DAY_TAG.search(tags or "")
            if not m or top(did) not in prog:
                continue
            p, k = prog[top(did)], int(m.group(1))
            p["days"] = max(p["days"] or 0, k)
            if typ != 0:
                p["day"] = max(p["day"] or 0, k)
    except sqlite3.Error:
        pass                    # 옛 모음 · 가짜 모음에 notes 가 없으면 Day 는 뺀다
    out = []
    for name, p in prog.items():
        if p["total"] <= p["learned"] and p["last"] < day0 - 30 * 86400:
            continue            # 다 본 지 한 달 넘은 덱은 진행 줄에서 뺀다
        out.append({"name": name, "total": p["total"], "learned": p["learned"], "new_today": p["new_today"],
                    "today": int(by_deck.get(name, 0)), "day": p["day"], "days": p["days"], "last": p["last"] or None})
    out.sort(key=lambda x: (-x["today"], -(x["last"] or 0), -x["total"]))
    return out[:PROGRESS_MAX]


def read_collection(path, now=None):
    """사본을 떠서 읽는다 — Anki 가 열어 둔 원본의 잠금과 부딪히지 않게."""
    tmp = tempfile.mkdtemp(prefix="horarium-anki-")
    try:
        for ext in ("", "-wal"):
            if os.path.exists(path + ext):
                shutil.copy2(path + ext, os.path.join(tmp, "collection.anki2" + ext))
        uri = "file:" + os.path.join(tmp, "collection.anki2").replace(os.sep, "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            return compute(con, now)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _post(url, payload, timeout):
    """AnkiConnect 요청 한 번. 응답 JSON 을 돌려준다(연결 안 되면 예외)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def connect_status():
    with _connect_lock:
        return dict(_connect)


def _errtext(e):
    """예외를 JSON 에 실을 수 있는 글로. Windows 소켓 오류문은 CP949 바이트가 대리 문자로 섞여 오므로 치환한다."""
    return ("%s: %s" % (type(e).__name__, e)).encode("utf-8", "replace").decode("utf-8")


def _sync_once(post, url, now):
    """AnkiConnect 에 version → sync. 결과를 _connect 에 남기고 캐시를 비운다."""
    try:
        v = post(url, {"action": "version", "version": 6}, 3)
        if not isinstance(v, dict) or v.get("result") is None:
            raise ValueError("version 응답이 이상함: %r" % (v,))
    except Exception as e:
        with _connect_lock:
            _connect.update({"present": False, "busy": False, "error": _errtext(e)})
        return False
    with _connect_lock:
        _connect.update({"present": True})
    try:
        r = post(url, {"action": "sync", "version": 6}, SYNC_TIMEOUT)
        err = r.get("error") if isinstance(r, dict) else None
        with _connect_lock:
            _connect.update({"busy": False, "error": err, "last_ok": (now if not err else _connect["last_ok"])})
    except Exception as e:
        with _connect_lock:
            _connect.update({"busy": False, "error": _errtext(e)})
        return False
    with _lock:
        _cache["mtime"] = None      # 다음 snapshot 이 파일을 다시 읽게
    return err is None


def maybe_sync(now=None, post=None, url=None, every=None, force=False, wait=False):
    """AnkiConnect 가 있으면 `every` 초에 한 번 sync 를 눌러 준다. 백그라운드(`wait=False`)가 기본.
    돌리기 시작했으면 True. AnkiConnect 가 없다고 판명된 뒤에도 `every` 마다 다시 물어본다(나중에 설치할 수 있으니)."""
    now = now or time.time()
    post = post or _post
    url = url or CONNECT_URL
    every = SYNC_EVERY_SEC if every is None else every
    with _connect_lock:
        if _connect["busy"]:
            return False
        if not force and _connect["last_try"] and now - _connect["last_try"] < every:   # last_try 0 = 아직 한 번도 안 함
            return False
        _connect.update({"busy": True, "last_try": now})
    if wait:
        return _sync_once(post, url, now)
    threading.Thread(target=_sync_once, args=(post, url, now), daemon=True).start()
    return True


def mirror_status():
    """§59 사본 상태 — 켜져 있지 않으면 None."""
    if anki_mirror is None or not anki_mirror.enabled():
        return None
    s = anki_mirror.status()
    return {"on": True, "busy": bool(s.get("busy")), "last_ok": s.get("last_ok") or None, "error": s.get("error")}


def snapshot(now=None, appdata=None, force=False, sync=None):
    """GET /api/anki 의 본체. 파일이 바뀌었거나 10분이 지났으면 다시 읽고, 아니면 캐시.
    §59 사본이 켜져 있으면(실제 환경에서만) 그 사본을 읽고 Orbit 이 직접 AnkiWeb 에서 받아 온다.
    아니면 §31 — 이 PC 의 Anki 파일을 읽고, `sync` 가 참이면 AnkiConnect 로 동기화를 눌러 본다."""
    now = now or time.time()
    if sync is None:
        sync = appdata is None
    mirror = appdata is None and anki_mirror is not None and anki_mirror.enabled()
    if mirror:
        if sync:
            anki_mirror.maybe_sync(now, force=force)
        path = anki_mirror.collection_path()
        if not os.path.exists(path):
            return {"available": False, "reason": "AnkiWeb 에서 처음 받는 중입니다", "data": None,
                    "connect": connect_status(), "mirror": mirror_status()}
        return _read_cached(path, now, force, mirror_status())
    if sync:
        maybe_sync(now, force=force)
    path = find_collection(appdata)
    if not path:
        return {"available": False, "reason": "Anki 데스크톱의 컬렉션을 찾지 못함 (%APPDATA%\\Anki2)", "data": None,
                "connect": connect_status(), "mirror": None}
    return _read_cached(path, now, force, None)


def _read_cached(path, now, force, mirror):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    with _lock:
        c = _cache
        fresh = (c["path"] == path and c["mtime"] == mtime and c["data"] is not None and now - c["at"] < CACHE_SEC)
        if fresh and not force:
            return {"available": True, "path": path, "mtime": mtime, "data": c["data"], "cached": True, "connect": connect_status(), "mirror": mirror}
        try:
            data = read_collection(path, now)
        except Exception as e:
            return {"available": True, "path": path, "mtime": mtime, "data": c["data"], "error": "%s: %s" % (type(e).__name__, e),
                    "connect": connect_status(), "mirror": mirror}
        c.update({"path": path, "mtime": mtime, "at": now, "data": data})
        return {"available": True, "path": path, "mtime": mtime, "data": data, "cached": False, "connect": connect_status(), "mirror": mirror}
