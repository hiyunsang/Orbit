# -*- coding: utf-8 -*-
"""
tests/test_engine.py — engine.build_day / classify 단위 테스트 (합성 이벤트)

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import os
import sys
import copy
import json
import datetime
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402
import engine  # noqa: E402
from engine import build_day, classify  # noqa: E402

DATE = "2026-09-02"
BASE = datetime.datetime(2026, 9, 2)


def T(h, m=0, s=0):
    """2026-09-02 기준 시각 → epoch. h 는 24 이상도 허용(다음날)."""
    return (BASE + datetime.timedelta(hours=h, minutes=m, seconds=s)).timestamp()


CODE = "C:\\Users\\PC1\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"
CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
SYNC = "C:\\MAENG\\MAENG_Sync.exe"
ACRO = "C:\\Program Files\\Adobe\\Acrobat.exe"
KAKAO = "C:\\Users\\PC1\\AppData\\Local\\Kakao\\KakaoTalk.exe"
FOO = "C:\\tools\\foo.exe"
BAR = "C:\\tools\\bar.exe"

# priority 내림차순으로 정의 (rules.json 에 의존하지 않음)
RULES = [
    {"target": "title", "pattern": "lecture|강의", "is_regex": True, "category": "focus",
     "priority": 300, "idle_limit_sec": None, "enabled": True},
    {"target": "title", "pattern": "YouTube", "is_regex": False, "category": "distraction",
     "priority": 200, "idle_limit_sec": None, "enabled": True},
    {"target": "exe", "pattern": "Code.exe", "is_regex": False, "category": "focus",
     "priority": 100, "idle_limit_sec": None, "enabled": True},
    {"target": "exe", "pattern": "chrome.exe", "is_regex": False, "category": "neutral",
     "priority": 100, "idle_limit_sec": None, "enabled": True},
    {"target": "exe", "pattern": "MAENG_Sync.exe", "is_regex": False, "category": "experiment",
     "priority": 100, "idle_limit_sec": 86400, "enabled": True},
    {"target": "exe", "pattern": "Acrobat.exe", "is_regex": False, "category": "focus",
     "priority": 100, "idle_limit_sec": 1200, "enabled": True},
    {"target": "exe", "pattern": "^kakaotalk\\.exe$", "is_regex": True, "category": "neutral",
     "priority": 100, "idle_limit_sec": None, "enabled": True},
]


class Ev(object):
    """합성 이벤트 빌더. id 는 호출 순서(=DB 기록 순서)."""

    def __init__(self):
        self.events = []
        self._id = 0

    def add(self, kind, ts, exe="", title="", extra=None):
        self._id += 1
        self.events.append({
            "id": self._id, "ts": ts, "kind": kind, "exe": exe, "title": title,
            "pid": 0, "extra": json.dumps(extra) if extra is not None else None,
        })
        return self

    # 편의 메서드
    def start(self, ts):
        return self.add("start", ts)

    def stop(self, ts):
        return self.add("stop", ts)

    def app(self, ts, exe, title=""):
        return self.add("app", ts, exe, title)


def session(start, end=None, last_seen=None, reason=None, sid=1):
    return {"id": sid, "start_ts": start, "last_seen": last_seen if last_seen is not None else (end or start),
            "end_ts": end, "end_reason": reason}


def override(oid, start, end, label, created):
    return {"id": oid, "start_ts": start, "end_ts": end, "label": label, "note": "", "created_ts": created}


def cfg_copy():
    return copy.deepcopy(common.DEFAULT_CONFIG)


NOW_LATER = T(30)  # 다음날 10:00 — '오늘'이 아님


def run(events, sessions, overrides=None, now=NOW_LATER, date=DATE, cfg=None, rules=RULES):
    return build_day(date, events, sessions, overrides or [], rules, cfg or cfg_copy(), now)


def non_off(day):
    return [s for s in day["spans"] if s["category"] != "off"]


def brief(day):
    return [(s["category"], s["start"], s["end"]) for s in non_off(day)]


class ClassifyTest(unittest.TestCase):
    def test_exe_rule_uses_base_name_case_insensitive(self):
        cat, rule = classify(CODE.lower(), "main.py", RULES)
        self.assertEqual(cat, "focus")
        self.assertEqual(rule["pattern"], "Code.exe")

    def test_title_rule_beats_exe_rule(self):
        cat, rule = classify(CHROME, "고양이 영상 - YouTube", RULES)
        self.assertEqual(cat, "distraction")
        self.assertEqual(rule["target"], "title")
        cat, _ = classify(CHROME, "Google", RULES)
        self.assertEqual(cat, "neutral")

    def test_regex_priority_and_case(self):
        cat, _ = classify(CHROME, "CNC LECTURE 3 - YouTube", RULES)
        self.assertEqual(cat, "focus")
        cat, _ = classify(KAKAO, "[카카오톡]", RULES)
        self.assertEqual(cat, "neutral")

    def test_empty_and_no_match_unknown(self):
        self.assertEqual(classify("", "", RULES), ("unknown", None))
        self.assertEqual(classify(FOO, "something", RULES), ("unknown", None))
        self.assertEqual(classify("", "제목만 있음", RULES), ("unknown", None))

    def test_invalid_regex_ignored(self):
        rules = [{"target": "title", "pattern": "(unclosed", "is_regex": True, "category": "focus",
                  "priority": 500, "idle_limit_sec": None, "enabled": True}] + RULES
        cat, _ = classify(CODE, "(unclosed", rules)
        self.assertEqual(cat, "focus")  # exe 규칙으로 매치, 깨진 규칙은 무시
        cat, _ = classify(FOO, "(unclosed", rules)
        self.assertEqual(cat, "unknown")

    def test_disabled_and_bad_category(self):
        rules = [{"target": "exe", "pattern": "foo.exe", "category": "focus", "priority": 500,
                  "enabled": False},
                 {"target": "exe", "pattern": "foo.exe", "category": "nonsense", "priority": 400}]
        cat, rule = classify(FOO, "", rules)
        self.assertEqual(cat, "unknown")
        self.assertIsNotNone(rule)


class BuildDayTest(unittest.TestCase):
    def test_simple_app_switch(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "main.py")
              .app(T(10), CHROME, "고양이 - YouTube")
              .app(T(11), CODE, "engine.py")
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("distraction", T(10), T(11)),
            ("focus", T(11), T(12)),
        ])
        s0 = non_off(day)[0]
        self.assertEqual(s0["exe"], "Code.exe")
        self.assertEqual(s0["title"], "main.py")
        self.assertEqual(s0["source"], "auto")
        self.assertIsNone(s0["override_id"])
        self.assertEqual(s0["apps"][0]["exe"], "Code.exe")
        self.assertAlmostEqual(s0["apps"][0]["sec"], 3600, delta=0.5)
        # spans 는 start 오름차순, off 포함, window 로 클립
        starts = [s["start"] for s in day["spans"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(day["spans"][0]["category"], "off")
        self.assertEqual(day["spans"][0]["start"], day["window"]["start"])
        self.assertEqual(day["spans"][-1]["category"], "off")
        self.assertEqual(day["spans"][-1]["end"], day["window"]["end"])
        sm = day["summary"]
        self.assertEqual(sm["first_active"], T(9))
        self.assertEqual(sm["last_active"], T(12))
        self.assertAlmostEqual(sm["attend_sec"], 3 * 3600)
        self.assertAlmostEqual(sm["active_sec"], 3 * 3600)
        self.assertAlmostEqual(sm["distraction_sec"], 3600)
        self.assertAlmostEqual(sm["distraction_ratio"], 1.0 / 3.0, places=6)
        self.assertEqual(sm["distraction_entries"], 1)
        self.assertAlmostEqual(sm["off_sec"], 0)
        self.assertFalse(sm["in_progress"])
        self.assertAlmostEqual(sm["by_category"]["focus"], 7200)
        self.assertEqual(sm["unknown_top"], [])

    def test_idle_grace_focus_900(self):
        # 10:00 에 마지막 입력, 10:10 에 idle 감지(ts 소급 10:00), 10:30 복귀
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("idle_on", T(10))
              .add("idle_off", T(10, 30))
              .stop(T(11)))
        day = run(ev.events, [session(T(9), T(11), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10, 15)),
            ("away", T(10, 15), T(10, 30)),
            ("focus", T(10, 30), T(11)),
        ])
        self.assertAlmostEqual(day["summary"]["away_sec"], 900)
        self.assertAlmostEqual(day["summary"]["active_sec"], 6300)
        self.assertIsNone(non_off(day)[1]["exe"])

    def test_idle_grace_unknown_zero(self):
        ev = (Ev().start(T(9)).app(T(9), FOO, "뭔가")
              .add("idle_on", T(10))
              .add("idle_off", T(10, 30))
              .stop(T(11)))
        day = run(ev.events, [session(T(9), T(11), reason="stop")])
        self.assertEqual(brief(day), [
            ("unknown", T(9), T(10)),
            ("away", T(10), T(10, 30)),
            ("unknown", T(10, 30), T(11)),
        ])

    def test_idle_experiment_unlimited(self):
        ev = (Ev().start(T(9)).app(T(9), SYNC, "MAENG Sync")
              .add("idle_on", T(9, 30))
              .add("idle_off", T(13))
              .stop(T(14)))
        day = run(ev.events, [session(T(9), T(14), reason="stop")])
        self.assertEqual(brief(day), [("experiment", T(9), T(14))])
        self.assertAlmostEqual(day["summary"]["away_sec"], 0)
        self.assertAlmostEqual(day["summary"]["focus_longest_sec"], 5 * 3600)

    def test_rule_idle_limit_overrides_config(self):
        ev = (Ev().start(T(9)).app(T(9), ACRO, "paper.pdf")
              .add("idle_on", T(10))
              .add("idle_off", T(11))
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10, 20)),
            ("away", T(10, 20), T(11)),
            ("focus", T(11), T(12)),
        ])

    def test_idle_on_sorted_before_later_app_event(self):
        # DB 기록 순서: app(Code) 9:00 → app(chrome) 10:05 → idle_on(ts 소급 10:00, 10:10 기록)
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .app(T(10, 5), CHROME, "Google")
              .add("idle_on", T(10))
              .add("idle_off", T(11))
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        # (ts,id) 정렬 → idle_on(10:00) 이 app(10:05) 보다 먼저. focus 는 10:00~10:05 만 grace 적용,
        # 10:05 부터는 chrome(neutral, grace 0) 이고 idle_since=10:00 유지 → 바로 away
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10, 5)),
            ("away", T(10, 5), T(11)),
            ("neutral", T(11), T(12)),
        ])

    def test_idle_on_before_previous_idle_off(self):
        # 자리로 돌아와 마우스를 한 번만 건드리고(12:00:00) 다시 떠남: idle_off 는 폴링 시각 12:00:01,
        # 다음 idle_on 은 마지막 입력 시각 12:00:00 으로 소급 → ts 가 idle_off 보다 앞선다.
        # 엔진이 idle_on.ts 를 직전 idle_off.ts 로 clamp 해 두 번째 자리비움이 사라지지 않아야 한다.
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("idle_on", T(10))
              .add("idle_off", T(12, 0, 1))
              .add("idle_on", T(12, 0, 0))
              .add("idle_off", T(13))
              .stop(T(14)))
        day = run(ev.events, [session(T(9), T(14), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10, 15)),
            ("away", T(10, 15), T(12, 0, 1)),
            ("focus", T(12, 0, 1), T(12, 15, 1)),
            ("away", T(12, 15, 1), T(13)),
            ("focus", T(13), T(14)),
        ])
        # 입력 이벤트는 수정되지 않는다(순수 함수)
        self.assertEqual(ev.events[4]["ts"], T(12, 0, 0))

    def test_idle_on_before_previous_idle_off_after_unlock(self):
        # 잠금 해제 직후 바로 떠나는 경우: unlock 11:00, idle_off 11:00:01, idle_on 소급 11:00:00
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("lock", T(10)).add("unlock", T(11))
              .add("idle_off", T(11, 0, 1))
              .add("idle_on", T(11, 0, 0))
              .add("idle_off", T(13))
              .stop(T(14)))
        day = run(ev.events, [session(T(9), T(14), reason="stop")])
        cats = [c for c, _, _ in brief(day)]
        self.assertEqual(cats, ["focus", "away", "focus", "away", "focus"])
        self.assertAlmostEqual(day["summary"]["away_sec"], 3600 + (3600 * 2 - 900))

    def test_idle_on_before_start_is_clamped_to_start(self):
        # 트래커 시작 직후 이미 idle: 마지막 입력(8:30)이 start(9:00)보다 앞 → start 로 clamp
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("idle_on", T(8, 30))
              .add("idle_off", T(10))
              .stop(T(11)))
        day = run(ev.events, [session(T(9), T(11), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(9, 15)),
            ("away", T(9, 15), T(10)),
            ("focus", T(10), T(11)),
        ])

    def test_idle_on_after_resume_keeps_backdated_ts(self):
        # resume/unlock 은 clamp 하한이 아니다: 일시정지 중 떠난 소급 idle 은 유지
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("pause", T(10), extra={"until": T(11)})
              .add("resume", T(11))
              .add("idle_on", T(10, 30))
              .add("idle_off", T(12))
              .stop(T(13)))
        day = run(ev.events, [session(T(9), T(13), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("paused", T(10), T(11)),
            ("away", T(11), T(12)),    # idle_since=10:30 + grace 900 = 10:45 < 11:00 → 바로 away
            ("focus", T(12), T(13)),
        ])

    def test_distraction_entries_not_counted_across_away(self):
        # DESIGN §7-4: '직전 active 구간' 기준 — 딴짓 중 자리비움 뒤 같은 딴짓으로 돌아오면 새 진입이 아님
        ev = (Ev().start(T(9)).app(T(9), CHROME, "A - YouTube")
              .add("idle_on", T(9, 30)).add("idle_off", T(11))
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertEqual([c for c, _, _ in brief(day)], ["distraction", "away", "distraction"])
        self.assertEqual(day["summary"]["distraction_entries"], 1)

    def test_overnight_lock_state_from_previous_day_events(self):
        # 전날 19:00 잠금 → 22:00~04:00 이벤트 없음 → 09:00 해제. 전날 이벤트가 주어지면 04:00~09:00 은 away → absent
        ev = (Ev().start(T(-19)).app(T(-19), CODE, "a.py")
              .add("lock", T(-9))
              .add("unlock", T(9)).app(T(9), CODE, "a.py")
              .stop(T(12)))
        day = run(ev.events, [session(T(-19), T(12), reason="stop")])
        # §11.1: 출근 전 away 는 absent 로 바뀐다
        self.assertEqual(brief(day), [("absent", T(4), T(9)), ("focus", T(9), T(12))])
        self.assertEqual(day["summary"]["first_active"], T(9))
        self.assertAlmostEqual(day["summary"]["absent_sec"], 5 * 3600)
        self.assertAlmostEqual(day["summary"]["away_sec"], 0)

    def test_lock_is_away_even_with_grace(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("lock", T(10))
              .add("idle_on", T(10, 1))
              .add("unlock", T(10, 30))
              .add("idle_off", T(10, 30))
              .stop(T(11)))
        day = run(ev.events, [session(T(9), T(11), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("away", T(10), T(10, 30)),
            ("focus", T(10, 30), T(11)),
        ])

    def test_session_gap_is_off(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py").stop(T(10))
              .start(T(11)).app(T(11), CODE, "b.py").stop(T(12)))
        sessions = [session(T(9), T(10), reason="stop", sid=1), session(T(11), T(12), reason="stop", sid=2)]
        day = run(ev.events, sessions)
        self.assertEqual(brief(day), [("focus", T(9), T(10)), ("focus", T(11), T(12))])
        offs = [s for s in day["spans"] if s["category"] == "off"]
        self.assertIn((T(10), T(11)), [(s["start"], s["end"]) for s in offs])
        sm = day["summary"]
        self.assertAlmostEqual(sm["off_sec"], 3600)
        self.assertAlmostEqual(sm["attend_sec"], 3 * 3600)
        self.assertAlmostEqual(sm["active_sec"], 2 * 3600)
        # 서로 다른 세션의 focus 는 인접하지 않으므로 최장 집중은 1시간
        self.assertAlmostEqual(sm["focus_longest_sec"], 3600)

    def test_dead_session_coverage_ends_at_last_seen_plus_heartbeat(self):
        # 트래커가 죽음: end_ts None, last_seen 10:00, now 는 다음날 → 커버리지 10:00:30 까지
        ev = Ev().start(T(9)).app(T(9), CODE, "a.py")
        day = run(ev.events, [session(T(9), None, last_seen=T(10))])
        self.assertEqual(brief(day), [("focus", T(9), T(10, 0, 30))])
        self.assertFalse(day["summary"]["in_progress"])

    def test_alive_session_today_in_progress(self):
        now = T(11)
        ev = Ev().start(T(9)).app(T(9), CODE, "a.py")
        day = run(ev.events, [session(T(9), None, last_seen=T(10, 59, 30))], now=now)
        self.assertEqual(brief(day), [("focus", T(9), now)])
        sm = day["summary"]
        self.assertTrue(sm["in_progress"])
        self.assertAlmostEqual(sm["attend_sec"], 2 * 3600)
        self.assertEqual(sm["last_active"], now)
        # 같은 데이터라도 날짜가 '오늘'이 아니면 in_progress False
        day2 = run(ev.events, [session(T(9), None, last_seen=T(10, 59, 30))], now=T(11), date="2026-09-01")
        self.assertFalse(day2["summary"]["in_progress"])

    def test_day_boundary_clip_0400(self):
        # 전날 22:00 시작 → 다음날 05:00 종료. 두 날짜 모두 04:00 에서 잘린다.
        ev = Ev().start(T(-2)).app(T(-2), CODE, "밤샘.py").stop(T(5))
        sessions = [session(T(-2), T(5), reason="stop")]
        today = run(ev.events, sessions, date="2026-09-02")
        self.assertEqual(brief(today), [("focus", T(4), T(5))])
        self.assertEqual(today["window"]["start"], T(4))
        self.assertEqual(today["window"]["end"], T(28))
        yesterday = run(ev.events, sessions, date="2026-09-01")
        self.assertEqual(brief(yesterday), [("focus", T(-2), T(4))])
        for s in yesterday["spans"]:
            self.assertGreaterEqual(s["start"], T(-20))
            self.assertLessEqual(s["end"], T(4))

    def test_absorb_short_into_previous(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .app(T(9, 30), CHROME, "잠깐 - YouTube")
              .app(T(9, 30, 5), CODE, "a.py")
              .stop(T(10)))
        day = run(ev.events, [session(T(9), T(10), reason="stop")])
        self.assertEqual(brief(day), [("focus", T(9), T(10))])
        span = non_off(day)[0]
        apps = dict(((a["exe"], a["title"]), a["sec"]) for a in span["apps"])
        self.assertAlmostEqual(apps[("chrome.exe", "잠깐 - YouTube")], 5, delta=0.5)
        self.assertAlmostEqual(apps[("Code.exe", "a.py")], 3595, delta=0.5)
        # 진입 횟수는 흡수 전 원시 구간 기준
        self.assertEqual(day["summary"]["distraction_entries"], 1)
        self.assertAlmostEqual(day["summary"]["distraction_sec"], 0)

    def test_absorb_into_next_when_previous_is_off(self):
        ev = (Ev().start(T(9)).app(T(9), CHROME, "잠깐 - YouTube")
              .app(T(9, 0, 5), CODE, "a.py")
              .stop(T(10)))
        day = run(ev.events, [session(T(9), T(10), reason="stop")])
        self.assertEqual(brief(day), [("focus", T(9), T(10))])

    def test_paused_not_absorbed_and_counted(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("pause", T(10), extra={"until": T(10, 0, 5)})
              .add("resume", T(10, 0, 5))
              .add("pause", T(11), extra={"until": T(11, 30)})
              .add("resume", T(11, 30))
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("paused", T(10), T(10, 0, 5)),
            ("focus", T(10, 0, 5), T(11)),
            ("paused", T(11), T(11, 30)),
            ("focus", T(11, 30), T(12)),
        ])
        self.assertAlmostEqual(day["summary"]["paused_sec"], 1805)
        # paused 가 끼면 집중 연속이 끊긴다: 9:00~10:00 = 3600 이 최장 (10:00:05~11:00 은 3595)
        self.assertAlmostEqual(day["summary"]["focus_longest_sec"], 3600)

    def test_start_while_paused_tiny_pieces_join_paused(self):
        # 트래커가 시작하자마자 일시정지 상태로 들어가면 start→app→pause 사이 수십 ms 조각이 생긴다.
        # 그 조각(unknown/focus)은 paused 에 붙어 별도 span 으로 남지 않아야 한다.
        ev = (Ev().start(T(9)).app(T(9, 0, 0) + 0.02, CODE, "a.py")
              .add("pause", T(9, 0, 0) + 0.04, extra={"until": T(9, 30)})
              .add("resume", T(9, 30))
              .app(T(9, 30), CODE, "a.py")
              .stop(T(10)))
        day = run(ev.events, [session(T(9), T(10), reason="stop")])
        self.assertEqual(brief(day), [
            ("paused", T(9), T(9, 30)),
            ("focus", T(9, 30), T(10)),
        ])
        self.assertEqual(day["spans"][0]["apps"], [])
        self.assertAlmostEqual(day["summary"]["first_active"], T(9, 30))

    def test_remote_beats_lock_and_app(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .add("remote_on", T(10)).add("lock", T(10, 10))
              .add("unlock", T(10, 20)).add("remote_off", T(10, 30))
              .stop(T(11)))
        day = run(ev.events, [session(T(9), T(11), reason="stop")])
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("remote", T(10), T(10, 30)),
            ("focus", T(10, 30), T(11)),
        ])
        self.assertAlmostEqual(day["summary"]["remote_sec"], 1800)

    def test_override_split_and_later_wins(self):
        ev = Ev().start(T(9)).app(T(9), CODE, "a.py").stop(T(12))
        sessions = [session(T(9), T(12), reason="stop")]
        ov = [override(1, T(10), T(11), "meeting", created=T(12, 1))]
        day = run(ev.events, sessions, ov)
        self.assertEqual(brief(day), [
            ("focus", T(9), T(10)),
            ("meeting", T(10), T(11)),
            ("focus", T(11), T(12)),
        ])
        mid = non_off(day)[1]
        self.assertEqual(mid["source"], "override")
        self.assertEqual(mid["override_id"], 1)
        self.assertEqual(mid["exe"], "Code.exe")  # apps 유지
        self.assertAlmostEqual(mid["apps"][0]["sec"], 3600, delta=0.5)
        self.assertEqual(non_off(day)[0]["source"], "auto")
        sm = day["summary"]
        self.assertAlmostEqual(sm["by_category"]["focus"], 7200)
        self.assertAlmostEqual(sm["by_category"]["meeting"], 3600)
        self.assertAlmostEqual(sm["active_sec"], 3 * 3600)
        # 나중 override(created_ts 큼)가 겹치는 부분을 덮는다 — 목록 순서와 무관
        ov2 = [override(2, T(10, 30), T(11, 30), "break", created=T(12, 5)),
               override(1, T(10), T(11), "meeting", created=T(12, 1))]
        day2 = run(ev.events, sessions, ov2)
        self.assertEqual(brief(day2), [
            ("focus", T(9), T(10)),
            ("meeting", T(10), T(10, 30)),
            ("break", T(10, 30), T(11, 30)),
            ("focus", T(11, 30), T(12)),
        ])
        self.assertEqual([s["override_id"] for s in non_off(day2)], [None, 1, 2, None])

    def test_override_skips_off_and_invalid_label(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py").stop(T(10))
              .start(T(11)).app(T(11), CODE, "b.py").stop(T(12)))
        sessions = [session(T(9), T(10), reason="stop", sid=1), session(T(11), T(12), reason="stop", sid=2)]
        ov = [override(1, T(9, 30), T(11, 30), "experiment", created=T(13)),
              override(2, T(9), T(9, 10), "away", created=T(13, 1)),      # MANUAL 아님 → 무시
              override(3, T(9), T(9, 10), "nonsense", created=T(13, 2))]  # 무시
        day = run(ev.events, sessions, ov)
        self.assertEqual(brief(day), [
            ("focus", T(9), T(9, 30)),
            ("experiment", T(9, 30), T(10)),
            ("experiment", T(11), T(11, 30)),
            ("focus", T(11, 30), T(12)),
        ])
        offs = [(s["start"], s["end"]) for s in day["spans"] if s["category"] == "off"]
        self.assertIn((T(10), T(11)), offs)
        # 연속 focus+experiment 는 인접할 때만 이어진다: 9:00~10:00 = 3600
        self.assertAlmostEqual(day["summary"]["focus_longest_sec"], 3600)

    def test_distraction_entries(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .app(T(10), CHROME, "A - YouTube")
              .app(T(10, 30), CHROME, "B - YouTube")   # 딴짓 → 딴짓: 카운트 안 함
              .app(T(11), CODE, "a.py")
              .add("idle_on", T(11, 30)).add("idle_off", T(12))  # away 끼어도
              .app(T(12), CHROME, "C - YouTube")
              .app(T(12, 30), KAKAO, "[카카오톡]")
              .app(T(12, 31), CHROME, "D - YouTube")
              .stop(T(13)))
        day = run(ev.events, [session(T(9), T(13), reason="stop")])
        sm = day["summary"]
        self.assertEqual(sm["distraction_entries"], 3)
        self.assertAlmostEqual(sm["distraction_sec"], 3600 + 1800 + 29 * 60)
        cats = [c for c, _, _ in brief(day)]
        self.assertEqual(cats, ["focus", "distraction", "focus", "away", "distraction",
                                "neutral", "distraction"])

    def test_unknown_top_five(self):
        ev = Ev().start(T(9))
        t = T(9)
        durations = [("a.exe", "A", 600), ("b.exe", "B", 500), ("c.exe", "C", 400),
                     ("d.exe", "D", 300), ("e.exe", "E", 200), ("f.exe", "F", 100)]
        for exe, title, sec in durations:
            ev.app(t, "C:\\x\\" + exe, title)
            t += sec
        # a.exe 를 한 번 더 (합산 확인)
        ev.app(t, "C:\\x\\a.exe", "A")
        t += 60
        ev.stop(t)
        day = run(ev.events, [session(T(9), t, reason="stop")])
        top = day["summary"]["unknown_top"]
        self.assertEqual(len(top), 5)
        self.assertEqual([x["exe"] for x in top], ["a.exe", "b.exe", "c.exe", "d.exe", "e.exe"])
        self.assertAlmostEqual(top[0]["sec"], 660, delta=0.5)
        self.assertEqual(top[0]["title"], "A")
        # override 로 재분류된 구간은 unknown_top 에서 빠진다 (a.exe 는 마지막 60초만 남아 꼴찌로)
        ov = [override(1, T(9), T(9, 10), "focus", created=t + 1)]
        day2 = run(ev.events, [session(T(9), t, reason="stop")], ov)
        top2 = day2["summary"]["unknown_top"]
        self.assertEqual([x["exe"] for x in top2], ["b.exe", "c.exe", "d.exe", "e.exe", "f.exe"])
        self.assertNotIn("a.exe", [x["exe"] for x in top2])
        # a.exe 60초는 unknown 이긴 하지만 상위 5 밖 (f.exe 100초보다 작음)
        self.assertAlmostEqual(day2["summary"]["by_category"]["focus"], 600)

    def test_rule_priority_title_over_exe_in_build(self):
        ev = (Ev().start(T(9)).app(T(9), CHROME, "Google 검색")
              .app(T(10), CHROME, "CNC 강의 3편 - YouTube")
              .app(T(11), CHROME, "고양이 - YouTube")
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertEqual([c for c, _, _ in brief(day)], ["neutral", "focus", "distraction"])

    def test_focus_longest_run(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py")
              .app(T(10), SYNC, "sync")
              .app(T(11), CHROME, "X - YouTube")
              .app(T(11, 30), CODE, "b.py")
              .stop(T(12)))
        day = run(ev.events, [session(T(9), T(12), reason="stop")])
        self.assertAlmostEqual(day["summary"]["focus_longest_sec"], 7200)

    def test_no_events_no_sessions(self):
        day = run([], [])
        self.assertEqual(non_off(day), [])
        sm = day["summary"]
        self.assertIsNone(sm["first_active"])
        self.assertIsNone(sm["last_active"])
        self.assertEqual(sm["attend_sec"], 0)
        self.assertEqual(sm["active_sec"], 0)
        self.assertEqual(sm["distraction_ratio"], 0)
        self.assertEqual(sm["distraction_entries"], 0)
        self.assertFalse(sm["in_progress"])
        self.assertEqual(day["date"], DATE)

    def test_deterministic(self):
        ev = (Ev().start(T(9)).app(T(9), CODE, "a.py").app(T(10), CHROME, "Y - YouTube")
              .add("idle_on", T(10, 20)).add("idle_off", T(11)).app(T(11), FOO, "z")
              .stop(T(12)))
        sessions = [session(T(9), T(12), reason="stop")]
        ov = [override(1, T(9, 30), T(10, 30), "meeting", created=T(13))]
        d1 = run(ev.events, sessions, ov)
        d2 = run(list(reversed(ev.events)), sessions, ov)  # 입력 순서가 달라도 같은 결과
        self.assertEqual(json.dumps(d1, sort_keys=True), json.dumps(d2, sort_keys=True))

    def test_span_exe_is_base_name_and_off_has_no_apps(self):
        ev = Ev().start(T(9)).app(T(9), CODE, "a.py").stop(T(10))
        day = run(ev.events, [session(T(9), T(10), reason="stop")])
        for s in day["spans"]:
            if s["category"] == "off":
                self.assertEqual(s["apps"], [])
                self.assertIsNone(s["exe"])
            else:
                self.assertNotIn("\\", s["exe"])
                for a in s["apps"]:
                    self.assertNotIn("\\", a["exe"])


class AbsentTest(unittest.TestCase):
    """DESIGN §11.1 — 출근 전·퇴근 후 away → absent."""

    @staticmethod
    def _events():
        # 04:00~09:00 idle(전날부터 방치) → 09:00 활동 → 12:00~13:00 idle → 활동 → 18:00 idle ~ 다음날 04:00
        # FOO 는 unknown(grace 0) 이라 idle 시점 그대로 away 가 된다.
        return (Ev().start(T(-19)).app(T(-19), FOO, "x")
                .add("idle_on", T(-9))
                .add("idle_off", T(9)).app(T(9), CODE, "a.py")
                .add("idle_on", T(12))
                .add("idle_off", T(13)).app(T(13), CODE, "b.py")
                .add("idle_on", T(18)).app(T(18), FOO, "x")
                .events)

    def test_a_past_day_front_and_back_absent_lunch_away(self):
        sessions = [session(T(-19), None, last_seen=T(40))]  # 트래커 계속 살아있었음(과거 날짜)
        day = run(self._events(), sessions, now=T(40))  # 다음날 20:00 → 오늘 아님
        self.assertEqual(brief(day), [
            ("absent", T(4), T(9)),
            ("focus", T(9), T(12, 15)),        # focus grace 900
            ("away", T(12, 15), T(13)),
            ("focus", T(13), T(18)),
            ("absent", T(18), T(28)),
        ])
        sm = day["summary"]
        self.assertFalse(sm["in_progress"])
        self.assertAlmostEqual(sm["away_sec"], 45 * 60)
        self.assertAlmostEqual(sm["absent_sec"], 5 * 3600 + 10 * 3600)
        self.assertAlmostEqual(sm["by_category"]["absent"], 15 * 3600)
        self.assertAlmostEqual(sm["by_category"]["away"], 45 * 60)
        self.assertEqual(sm["first_active"], T(9))
        self.assertEqual(sm["last_active"], T(18))
        self.assertAlmostEqual(sm["active_sec"], 3 * 3600 + 15 * 60 + 5 * 3600)
        # absent 는 활동시간·자리비움 어디에도 안 들어감
        self.assertAlmostEqual(sm["attend_sec"], 9 * 3600)
        for s in day["spans"]:
            if s["category"] == "absent":
                self.assertEqual(s["source"], "auto")
                self.assertIsNone(s["override_id"])

    def test_a_lock_based_away_also_converted(self):
        # idle 이 아니라 lock 으로 생긴 앞·뒤 away 도 absent
        ev = (Ev().start(T(-19)).app(T(-19), CODE, "a.py")
              .add("lock", T(-9))
              .add("unlock", T(9)).app(T(9), CODE, "a.py")
              .add("lock", T(18))
              .events)
        day = run(ev, [session(T(-19), None, last_seen=T(40))], now=T(40))
        self.assertEqual(brief(day), [
            ("absent", T(4), T(9)),
            ("focus", T(9), T(18)),
            ("absent", T(18), T(28)),
        ])
        self.assertAlmostEqual(day["summary"]["absent_sec"], 15 * 3600)
        self.assertAlmostEqual(day["summary"]["away_sec"], 0)

    def test_b_today_in_progress_keeps_trailing_away(self):
        now = T(19)
        sessions = [session(T(-19), None, last_seen=T(18, 59, 50))]
        day = run(self._events(), sessions, now=now)
        self.assertEqual(brief(day), [
            ("absent", T(4), T(9)),
            ("focus", T(9), T(12, 15)),
            ("away", T(12, 15), T(13)),
            ("focus", T(13), T(18)),
            ("away", T(18), T(19)),
        ])
        sm = day["summary"]
        self.assertTrue(sm["in_progress"])
        self.assertAlmostEqual(sm["away_sec"], 45 * 60 + 3600)
        self.assertAlmostEqual(sm["absent_sec"], 5 * 3600)
        self.assertEqual(sm["first_active"], T(9))
        self.assertEqual(sm["last_active"], T(18))

    def test_c_no_activity_all_absent(self):
        ev = (Ev().start(T(-19)).app(T(-19), FOO, "x")
              .add("idle_on", T(-9))
              .events)
        day = run(ev, [session(T(-19), None, last_seen=T(40))], now=T(40))
        self.assertEqual(brief(day), [("absent", T(4), T(28))])
        sm = day["summary"]
        self.assertIsNone(sm["first_active"])
        self.assertIsNone(sm["last_active"])
        self.assertAlmostEqual(sm["absent_sec"], 24 * 3600)
        self.assertAlmostEqual(sm["away_sec"], 0)
        self.assertAlmostEqual(sm["active_sec"], 0)
        self.assertAlmostEqual(sm["attend_sec"], 0)

    def test_c_no_activity_today_in_progress_all_absent(self):
        # first_active None 이면 in_progress 여도 모든 away → absent
        ev = (Ev().start(T(-19)).app(T(-19), FOO, "x")
              .add("idle_on", T(-9))
              .events)
        now = T(10)
        day = run(ev, [session(T(-19), None, last_seen=T(9, 59, 50))], now=now)
        self.assertEqual(brief(day), [("absent", T(4), T(10))])
        self.assertTrue(day["summary"]["in_progress"])
        self.assertAlmostEqual(day["summary"]["absent_sec"], 6 * 3600)

    def test_override_on_absent_span_becomes_active_and_moves_first_active(self):
        # 일찍 와서 PC 안 쓰고 실험: absent 구간을 override 로 experiment 태깅 → active 가 되어 출근이 앞당겨진다
        sessions = [session(T(-19), None, last_seen=T(40))]
        base = run(self._events(), sessions, now=T(40))
        self.assertEqual(base["summary"]["first_active"], T(9))
        ov = [override(1, T(7), T(9), "experiment", created=T(20))]
        day = run(self._events(), sessions, ov, now=T(40))
        self.assertEqual(brief(day)[:3], [
            ("absent", T(4), T(7)),
            ("experiment", T(7), T(9)),
            ("focus", T(9), T(12, 15)),
        ])
        sm = day["summary"]
        self.assertEqual(sm["first_active"], T(7))
        self.assertEqual(sm["last_active"], T(18))
        self.assertAlmostEqual(sm["absent_sec"], 3 * 3600 + 10 * 3600)
        self.assertAlmostEqual(sm["attend_sec"], 11 * 3600)
        exp = [s for s in day["spans"] if s["category"] == "experiment"][0]
        self.assertEqual(exp["source"], "override")
        self.assertEqual(exp["override_id"], 1)
        # override 가 덮지 않은 absent 조각(04:00~07:00)은 그대로 absent
        self.assertEqual(day["spans"][0]["category"], "absent")

    def test_override_on_trailing_absent_moves_last_active(self):
        sessions = [session(T(-19), None, last_seen=T(40))]
        ov = [override(2, T(19), T(20), "meeting", created=T(21))]
        day = run(self._events(), sessions, ov, now=T(40))
        # 퇴근이 20:00 으로 늦춰지므로 18:00~19:00 은 first/last_active 사이 → away 로 남는다
        self.assertEqual(brief(day)[-3:], [
            ("away", T(18), T(19)),
            ("meeting", T(19), T(20)),
            ("absent", T(20), T(28)),
        ])
        self.assertEqual(day["summary"]["last_active"], T(20))
        self.assertAlmostEqual(day["summary"]["absent_sec"], 5 * 3600 + 8 * 3600)
        self.assertAlmostEqual(day["summary"]["away_sec"], 45 * 60 + 3600)

    def test_paused_remote_off_untouched(self):
        # 앞쪽에 paused / remote 가 있어도 absent 로 바뀌지 않는다
        ev = (Ev().start(T(5)).app(T(5), CODE, "a.py")
              .add("pause", T(5), extra={"until": T(6)})
              .add("resume", T(6))
              .add("remote_on", T(6)).add("remote_off", T(7))
              .add("lock", T(7)).add("unlock", T(9)).app(T(9), CODE, "a.py")
              .stop(T(12)))
        day = run(ev.events, [session(T(5), T(12), reason="stop")])
        cats = [c for c, _, _ in brief(day)]
        self.assertEqual(cats, ["paused", "remote", "absent", "focus"])
        sm = day["summary"]
        self.assertAlmostEqual(sm["paused_sec"], 3600)
        self.assertAlmostEqual(sm["remote_sec"], 3600)
        self.assertAlmostEqual(sm["absent_sec"], 2 * 3600)
        self.assertAlmostEqual(sm["away_sec"], 0)
        # 세션 밖(off)은 그대로 off
        self.assertIn("off", [s["category"] for s in day["spans"]])

    def test_summary_has_absent_keys_even_when_empty(self):
        day = run([], [])
        self.assertEqual(day["summary"]["absent_sec"], 0)
        self.assertIn("absent", day["summary"]["by_category"])
        self.assertIn("away", day["summary"]["by_category"])


if __name__ == "__main__":
    unittest.main()
