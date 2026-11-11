# -*- coding: utf-8 -*-
"""
remote_jump.py — Jump Desktop Connect 이벤트 로그 파싱 + 원격 구간 계산 (순수 함수, win32 import 없음)

Jump Desktop Connect 는 Application 채널(Provider 'Jump Desktop Connect', EventID 0)에
<EventData><Data> 하나로 다음 문자열을 남긴다(오타 'Incomming' 원문):
  Incomming Connection Request: Type:Cloud, ConnectionID:xxxx, PeerID:CD-…, Email:…, IP:1.2.3.4
  Incomming Connection Established: Type:Cloud, ConnectionID:xxxx, TunnelID:rtc, PeerID:CD-…, Email:…
  Authentication Succeeded: ConnectionID:xxxx, Type:Local, LocalUser:PC1
  Connection closed: ConnectionID:xxxx, PeerID:CD-…

DESIGN.md §13.1, §13.3 이 계약이다.
"""
import re
import datetime
import xml.etree.ElementTree as ET

EVT_NS = "http://schemas.microsoft.com/win/2004/08/events/event"
_NS = {"e": EVT_NS}

_RE_CONN = re.compile(r"ConnectionID:([A-Za-z0-9_\-]+)")
_RE_PEER = re.compile(r"PeerID:([A-Za-z0-9_\-]+)")
_RE_IP = re.compile(r"IP:([0-9A-Fa-f.:]+)")

# (문자열 접두, kind) — 대소문자 무시, 앞쪽 공백 허용
_KINDS = (
    ("incomming connection request", "request"),
    ("incoming connection request", "request"),
    ("incomming connection established", "established"),
    ("incoming connection established", "established"),
    ("authentication succeeded", "auth"),
    ("connection closed", "closed"),
)


def parse_system_time(s):
    """'2026-09-02T16:22:29.2848908Z' (UTC, 소수 7자리) → 로컬 epoch(float). 실패 시 None."""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    frac = ""
    if "." in s:
        s, frac = s.split(".", 1)
        frac = re.sub(r"[^0-9]", "", frac)[:6]
    try:
        dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    if frac:
        dt = dt.replace(microsecond=int(frac.ljust(6, "0")))
    dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def classify_data(text):
    """Data 문자열 → kind 또는 None."""
    t = (text or "").strip().lower()
    for prefix, kind in _KINDS:
        if t.startswith(prefix):
            return kind
    return None


def parse_jump_event(xml_text):
    """EvtRender(EventXml) 결과 → dict 또는 None(파싱 실패 / 관심 없는 문자열).
    {"ts": epoch(local), "record_id": int, "kind": "request"|"established"|"auth"|"closed",
     "conn": "ci5pucp6", "peer": str|None, "ip": str|None}"""
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    system = root.find("e:System", _NS)
    if system is None:
        # 네임스페이스 없이 온 경우도 허용
        system = root.find("System")
        ns_ok = False
    else:
        ns_ok = True

    def _find(parent, tag):
        if parent is None:
            return None
        el = parent.find("e:" + tag, _NS) if ns_ok else None
        if el is None:
            el = parent.find(tag)
        return el

    tc = _find(system, "TimeCreated")
    ts = parse_system_time(tc.get("SystemTime")) if tc is not None else None
    if ts is None:
        return None
    rid_el = _find(system, "EventRecordID")
    try:
        record_id = int((rid_el.text or "").strip()) if rid_el is not None else 0
    except ValueError:
        record_id = 0

    ed = _find(root, "EventData")
    data_texts = []
    if ed is not None:
        for d in list(ed):
            if d.text:
                data_texts.append(d.text)
    data = " ".join(data_texts).strip()
    kind = classify_data(data)
    if kind is None:
        return None
    m = _RE_CONN.search(data)
    if not m:
        return None
    conn = m.group(1)
    mp = _RE_PEER.search(data)
    mi = _RE_IP.search(data)
    return {
        "ts": ts,
        "record_id": record_id,
        "kind": kind,
        "conn": conn,
        "peer": mp.group(1) if mp else None,
        "ip": mi.group(1) if mi else None,
    }


def build_intervals(events, max_session_hours, now_ts, force_close_ts=None):
    """이벤트 목록 → [(start_ts, end_ts|None, conn), ...] (start 오름차순).

    - established 로 열고 같은 conn 의 closed 로 닫는다.
    - closed 가 없으면: 같은 peer 의 다음 established 시각, start+max_session_hours,
      force_close_ts(부팅 시각 — 그 전에 열린 conn 은 재부팅으로 끊긴 것) 중 이른 시각으로 닫는다.
      아무것도 없고(또는 모두 now 보다 뒤) → end=None(진행 중).
    - request/auth 는 무시. 같은 conn 의 established 중복은 첫 것만.
    - closed 가 established 보다 앞서는 이상 데이터는 무시(구간 안 만듦).
    """
    cap = float(max_session_hours) * 3600.0 if max_session_hours else None
    evs = [e for e in (events or []) if e and e.get("kind") in ("established", "closed")]
    evs.sort(key=lambda e: (float(e["ts"]), int(e.get("record_id") or 0)))

    starts = []     # [(ts, conn, peer)] 순서대로
    seen_conn = set()
    closed_at = {}  # conn -> 첫 closed ts (established 이후 것만)
    opened = {}
    for e in evs:
        conn = e.get("conn")
        if not conn:
            continue
        if e["kind"] == "established":
            if conn in seen_conn:
                continue
            seen_conn.add(conn)
            opened[conn] = float(e["ts"])
            starts.append((float(e["ts"]), conn, e.get("peer")))
        else:  # closed
            if conn in opened and conn not in closed_at and float(e["ts"]) >= opened[conn]:
                closed_at[conn] = float(e["ts"])

    out = []
    for i, (st, conn, peer) in enumerate(starts):
        end = closed_at.get(conn)
        if end is None:
            candidates = []
            if cap is not None:
                candidates.append(st + cap)
            if force_close_ts is not None and float(force_close_ts) > st:
                candidates.append(float(force_close_ts))
            if peer:
                for st2, conn2, peer2 in starts[i + 1:]:
                    if peer2 == peer and st2 > st:
                        candidates.append(st2)
                        break
            candidates = [c for c in candidates if now_ts is None or c <= now_ts]
            end = min(candidates) if candidates else None
        if end is not None and end < st:
            continue
        out.append((st, end, conn))
    return out


def open_connections(events, max_session_hours, now_ts, force_close_ts=None):
    """현재(now_ts) 열려 있는 conn → start_ts. 시작 시 상태 복원용."""
    out = {}
    for st, end, conn in build_intervals(events, max_session_hours, now_ts, force_close_ts):
        if end is None:
            out[conn] = st
    return out
