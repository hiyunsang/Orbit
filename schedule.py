# -*- coding: utf-8 -*-
"""
schedule.py — 홍익대 대학원 학사일정 (DESIGN §24)

대학원 학사일정 페이지(https://www.hongik.ac.kr/kr/education/academic-schedule-grad.do)가 캘린더를 채울 때 쓰는
내부 API 를 그대로 부른다:
  POST /sso/APICipher.jsp  data={"url":"/schedule/get_schedule.php","url2":"&GUBUN=","url3":"1","url4":"&YEAR=","url5":"2026"}
  → {"result":"Y","data":[{"START":"2026-09-01","END":"2026-09-07","TITLE":"<percent-encoded>","GUBUN":"9","HOLY":"0",...}]}
GUBUN 2 = 대학원 일정, 9 = 공통(개강·종강·공휴일). TITLE 은 percent 인코딩된 UTF-8.

표준 라이브러리만. 연도별 JSON 캐시(data/학사일정_<year>.json)를 두고 하루 한 번만 다시 받는다. 실패하면 캐시.
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import common

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(s):
    if not isinstance(s, str) or not _DATE_RE.match(s):
        return False
    try:
        datetime.datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False

API_URL = "https://www.hongik.ac.kr/sso/APICipher.jsp"
REFERER = "https://www.hongik.ac.kr/kr/education/academic-schedule-grad.do"
REFRESH_SEC = 24 * 3600
TIMEOUT = 20
GUBUN_NAME = {"2": "대학원", "9": "공통"}

_lock = threading.Lock()
_fetching = set()


def cache_path(year, cfg=None):
    return os.path.join(common.data_dir(cfg), "학사일정_%s.json" % year)


def parse_items(raw):
    """API 응답의 data 목록 → [{start, end, title, gubun, holiday}] (시작일·종료일 순). 순수 함수."""
    out = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        start, end = str(it.get("START") or "")[:10], str(it.get("END") or "")[:10]
        if not valid_date(start):
            continue
        if not valid_date(end) or end < start:
            end = start
        title = urllib.parse.unquote(str(it.get("TITLE") or "")).strip()
        if not title:
            continue
        gubun = str(it.get("GUBUN") or "")
        out.append({"start": start, "end": end, "title": title, "gubun": gubun,
                    "gubun_name": GUBUN_NAME.get(gubun, ""), "holiday": str(it.get("HOLY") or "0") == "1"})
    out.sort(key=lambda x: (x["start"], x["end"], x["title"]))
    return out


def fetch_year(year, opener=None):
    """연도 하나를 받아 파싱. 실패하면 예외."""
    payload = json.dumps({"url": "/schedule/get_schedule.php", "url2": "&GUBUN=", "url3": "1",
                          "url4": "&YEAR=", "url5": str(year)})
    req = urllib.request.Request(
        API_URL, data=urllib.parse.urlencode({"data": payload}).encode("utf-8"),
        headers={"User-Agent": "Mozilla/5.0 Horarium", "Referer": REFERER, "X-Requested-With": "XMLHttpRequest"})
    fn = opener or urllib.request.urlopen
    body = fn(req, timeout=TIMEOUT).read().decode("utf-8", "replace").strip()
    d = json.loads(body)
    if d.get("result") != "Y":
        raise RuntimeError("학사일정 API 응답 result=%r" % d.get("result"))
    return parse_items(d.get("data"))


def load_cache(year, cfg=None):
    p = cache_path(year, cfg)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_cache(year, items, cfg=None):
    p = cache_path(year, cfg)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"year": str(year), "fetched": time.time(), "items": items}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _refresh_bg(year, cfg):
    try:
        items = fetch_year(year)
        with _lock:
            save_cache(year, items, cfg)
    except Exception:
        pass
    finally:
        _fetching.discard(str(year))


def items_for(year, cfg=None, now=None):
    """캐시를 돌려주고, 하루가 지났으면 뒤에서 새로 받는다. 캐시가 없으면 그 자리에서 받는다(첫 실행)."""
    now = now or time.time()
    c = load_cache(year, cfg)
    if c is None:
        try:
            items = fetch_year(year)
            save_cache(year, items, cfg)
            return items, now, None
        except Exception as e:
            return [], 0, str(e)[:200]
    if now - float(c.get("fetched") or 0) > REFRESH_SEC and str(year) not in _fetching:
        _fetching.add(str(year))
        threading.Thread(target=_refresh_bg, args=(year, cfg), daemon=True).start()
    return c.get("items") or [], float(c.get("fetched") or 0), None


def upcoming(items, date, days_ahead=90, limit=None):
    """date 기준 진행 중이거나 앞으로 days_ahead 일 안에 시작하는 것. 각 항목에 d(시작까지 남은 일수, 진행 중이면 0 이하)·ongoing."""
    d0 = datetime.datetime.strptime(date, "%Y-%m-%d").date()
    horizon = (d0 + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    out = []
    for it in items:
        if it["end"] < date or it["start"] > horizon:
            continue
        ds = (datetime.datetime.strptime(it["start"], "%Y-%m-%d").date() - d0).days
        de = (datetime.datetime.strptime(it["end"], "%Y-%m-%d").date() - d0).days
        x = dict(it)
        x["d"] = ds
        x["d_end"] = de
        x["ongoing"] = ds <= 0 <= de
        out.append(x)
    out.sort(key=lambda x: (0 if x["ongoing"] else 1, x["end"] if x["ongoing"] else x["start"], x["title"]))
    return out[:limit] if limit else out


def api_get(date, cfg=None):
    """GET /api/schedule?date=D 의 본체. 해당 연도 + 12월이면 다음 연도까지."""
    years = [date[:4]]
    if date[5:7] >= "11":
        years.append(str(int(date[:4]) + 1))
    items, fetched, err = [], 0, None
    for y in years:
        it, f, e = items_for(y, cfg)
        items += it
        fetched = max(fetched, f)
        err = err or e
    up = upcoming(items, date)
    return {"date": date, "items": up, "fetched": fetched, "error": err, "source": REFERER}
