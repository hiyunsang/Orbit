# -*- coding: utf-8 -*-
"""
tests/test_names.py — DESIGN §15 표시 이름: engine.display_name + build_day 의 label / apps.name / unknown_top.name
+ common.normalize_names / load_names. names.default.json 을 직접 읽어 실제 기본 규칙으로 검사한다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import os
import sys
import copy
import json
import shutil
import datetime
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common  # noqa: E402
import engine  # noqa: E402
from engine import build_day, display_name  # noqa: E402

DATE = "2026-09-02"
BASE = datetime.datetime(2026, 9, 2)


def T(h, m=0, s=0):
    return (BASE + datetime.timedelta(hours=h, minutes=m, seconds=s)).timestamp()


CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
PPT = "C:\\Program Files\\Microsoft Office\\root\\Office16\\POWERPNT.EXE"
FOO = "C:\\tools\\foo.exe"
TERM = "C:\\Program Files\\WindowsApps\\Microsoft.WindowsTerminal\\WindowsTerminal.exe"

with open(common.NAMES_DEFAULT_PATH, "r", encoding="utf-8") as _f:
    DEFAULT_RAW = json.load(_f)
NAMES = common.normalize_names(DEFAULT_RAW)

RULES = [
    {"target": "title", "pattern": "YouTube", "is_regex": False, "category": "distraction",
     "priority": 200, "idle_limit_sec": None, "enabled": True},
    {"target": "exe", "pattern": "POWERPNT.EXE", "is_regex": False, "category": "focus",
     "priority": 100, "idle_limit_sec": None, "enabled": True},
]


class Ev(object):
    def __init__(self):
        self.events = []
        self._id = 0

    def add(self, kind, ts, exe="", title=""):
        self._id += 1
        self.events.append({"id": self._id, "ts": ts, "kind": kind, "exe": exe, "title": title,
                            "pid": 0, "extra": None})
        return self

    def start(self, ts):
        return self.add("start", ts)

    def stop(self, ts):
        return self.add("stop", ts)

    def app(self, ts, exe, title=""):
        return self.add("app", ts, exe, title)


def session(start, end, sid=1):
    return {"id": sid, "start_ts": start, "last_seen": end, "end_ts": end, "end_reason": "stop"}


def override(oid, start, end, label, created):
    return {"id": oid, "start_ts": start, "end_ts": end, "label": label, "note": "", "created_ts": created}


class DisplayNameTest(unittest.TestCase):
    """§15.2 의 예시 그대로."""

    def n(self, exe, title):
        return display_name(exe, title, NAMES)

    def test_chatgpt_tab(self):
        self.assertEqual(self.n(CHROME, "ChatGPT - Chrome"), "ChatGPT")
        self.assertEqual(self.n(CHROME, "ChatGPT: Chat, Work, Create & Code with AI - Chrome"), "ChatGPT")

    def test_claude_tab_and_app(self):
        self.assertEqual(self.n(CHROME, "New chat - Claude - Chrome"), "Claude")
        self.assertEqual(self.n("claude.exe", "Claude"), "Claude")

    def test_scholar_korean_and_mail_reply(self):
        # 통합 검증에서 보강한 흔한 규칙: Google 학술 검색 → Google Scholar, 답장/전달 메일 제목 → 메일
        self.assertEqual(self.n(CHROME, "Google 학술 검색 - Chrome"), "Google Scholar")
        self.assertEqual(self.n(CHROME, "Influence of grain... - Google 학술 검색 - Chrome"), "Google Scholar")
        self.assertEqual(self.n(CHROME, "Re: 논문 보내드립니다 - Chrome"), "메일")
        self.assertEqual(self.n(CHROME, "RE: Re: 시편 구매 문의 - Chrome"), "메일")
        self.assertEqual(self.n(CHROME, "RESULT page - Chrome"), "RESULT page")  # 'RE' 로 시작만 해선 안 잡힘

    def test_athenaeum_reader_edge(self):
        self.assertEqual(self.n(EDGE, "Ma 2026 · Athenaeum 외 페이지 3개 - 프로필 1 - Microsoft Edge"), "Athenaeum")
        self.assertEqual(self.n(EDGE, "Goel 2015 읽기 - 프로필 1 - Microsoft Edge"), "Athenaeum")
        self.assertEqual(self.n(EDGE, "Paperdesk"), "Athenaeum")
        self.assertEqual(self.n(EDGE, "MAENG 논문 검색"), "Athenaeum")

    def test_youtube_with_counter_prefix(self):
        self.assertEqual(self.n(CHROME, "(3) 재밌는 영상 - YouTube - Chrome"), "YouTube")

    def test_naver_blog(self):
        self.assertEqual(self.n(CHROME, "도쿄 완벽한 하루 : 네이버 블로그 - Chrome"), "네이버")
        self.assertEqual(self.n(CHROME, "NAVER - Chrome"), "네이버")

    def test_misumi_meviy_strip_only(self):
        self.assertEqual(self.n(CHROME, "프로젝트 일람 | meviy(메비) | 한국미스미 - Chrome"),
                         "프로젝트 일람 | meviy(메비) | 한국미스미")

    def test_new_tab(self):
        self.assertEqual(self.n(CHROME, "새 탭 - Chrome"), "새 탭")
        self.assertEqual(self.n(EDGE, "새 탭 - 프로필 1 - Microsoft Edge"), "새 탭")

    def test_browser_empty_title_falls_back_to_browser_name(self):
        self.assertEqual(self.n(EDGE, ""), "Edge")
        self.assertEqual(self.n(CHROME, None), "Chrome")
        self.assertEqual(self.n(CHROME, "Google Chrome"), "Chrome")  # 제목이 브라우저 이름뿐

    def test_exe_table(self):
        self.assertEqual(self.n(PPT, "논문 figure.pptx - PowerPoint"), "PowerPoint")
        self.assertEqual(self.n("powerpnt.exe", ""), "PowerPoint")  # 대소문자 무시

    def test_unknown_exe_strips_suffix(self):
        self.assertEqual(self.n(FOO, "whatever"), "foo")
        self.assertEqual(self.n("Bar.EXE", ""), "Bar")

    def test_no_window(self):
        self.assertEqual(self.n("", ""), u"(창 없음)")
        self.assertEqual(self.n(None, None), u"(창 없음)")

    def test_names_none_means_exe_only(self):
        self.assertEqual(display_name(CHROME, "ChatGPT - Chrome", None), "chrome")
        self.assertEqual(display_name(PPT, "x - PowerPoint"), "POWERPNT")
        self.assertEqual(display_name("", "", None), u"(창 없음)")

    def test_title_rule_with_exe_restriction(self):
        # 터미널의 'claude' 탭은 Claude Code, Claude 데스크톱 앱은 Claude
        self.assertEqual(self.n(TERM, "claude"), "Claude Code")
        self.assertEqual(self.n(TERM, "C:\\Users\\PC1\\.local\\bin\\claude.EXE"), "Claude Code")
        self.assertEqual(self.n("claude.exe", "Claude"), "Claude")
        self.assertEqual(self.n("explorer.exe", "Program Manager"), u"바탕 화면")
        self.assertEqual(self.n("explorer.exe", "다운로드 - 파일 탐색기"), u"탐색기")

    def test_long_browser_title_truncated_to_40(self):
        long_title = "Influence of grain morphology on the chip formation mechanisms of wrought alloy 718 - Chrome"
        out = self.n(CHROME, long_title)
        self.assertLessEqual(len(out), 40)
        self.assertTrue(out.endswith(u"…"))
        self.assertTrue(long_title.startswith(out[:-1].rstrip()))

    def test_invalid_regex_ignored(self):
        names = {"browsers": ["chrome.exe"], "strip": ["(bad", "\\s*-\\s*Chrome$"],
                 "title": [{"pattern": "[", "name": "BAD", "is_regex": True},
                           {"pattern": "Gmail", "name": "Gmail"}],
                 "exe": {}}
        self.assertEqual(display_name(CHROME, "받은편지함 - Gmail - Chrome", names), "Gmail")
        self.assertEqual(display_name(CHROME, "새 탭 - Chrome", names), "새 탭")

    def test_raw_unnormalized_names_accepted(self):
        # normalize_names 를 거치지 않은 원본 dict 도 동작(대소문자·is_regex 기본값)
        self.assertEqual(display_name("POWERPNT.EXE", "", DEFAULT_RAW), "PowerPoint")
        self.assertEqual(display_name(CHROME, "YouTube - Chrome", DEFAULT_RAW), "YouTube")


class BuildDayNamesTest(unittest.TestCase):
    def _day(self, names):
        ev = (Ev().start(T(9))
              .app(T(9), CHROME, "New chat - Claude - Chrome")
              .app(T(9, 30), PPT, "논문 figure.pptx - PowerPoint")
              .app(T(10), CHROME, "재밌는 영상 - YouTube - Chrome")
              .app(T(10, 20), FOO, "foo window")   # unknown 20분 (< Claude 탭 30분)
              .stop(T(10, 40)))
        cfg = copy.deepcopy(common.DEFAULT_CONFIG)
        return build_day(DATE, ev.events, [session(T(9), T(10, 40))], [override(1, T(9, 40), T(9, 50), "meeting", T(12))],
                         RULES, cfg, T(30), names=names)

    def test_label_apps_name_unknown_top_name(self):
        day = self._day(NAMES)
        spans = [s for s in day["spans"] if s["category"] != "off"]
        self.assertTrue(spans)
        for s in spans:
            self.assertIn("label", s)
            for a in s["apps"]:
                self.assertIn("name", a)
                self.assertEqual(a["name"], display_name(a["exe"], a["title"], NAMES))
            if s["apps"]:
                self.assertEqual(s["label"], s["apps"][0]["name"])
        by_cat = {}
        for s in spans:  # 카테고리별 첫 구간 (unknown 은 Claude 탭 → foo 순으로 둘)
            by_cat.setdefault(s["category"], s)
        self.assertEqual(by_cat["unknown"]["label"], "Claude")       # chrome 은 규칙 없음 → unknown, 이름은 Claude
        self.assertEqual(by_cat["focus"]["label"], "PowerPoint")
        self.assertEqual(by_cat["distraction"]["label"], "YouTube")
        # override 로 바뀐 span 도 label 유지
        self.assertEqual(by_cat["meeting"]["source"], "override")
        self.assertEqual(by_cat["meeting"]["label"], "PowerPoint")
        top = day["summary"]["unknown_top"]
        self.assertTrue(top)
        self.assertEqual(top[0]["name"], "Claude")
        self.assertIn("foo", [t["name"] for t in top])

    def test_names_none_keeps_shape_and_exe_label(self):
        day = self._day(None)
        spans = [s for s in day["spans"] if s["category"] != "off"]
        labels = {}
        for s in spans:
            labels.setdefault(s["category"], s["label"])
        self.assertEqual(labels["unknown"], "chrome")
        self.assertEqual(labels["focus"], "POWERPNT")
        self.assertEqual(day["summary"]["unknown_top"][0]["name"], "chrome")

    def test_names_do_not_change_categories(self):
        a = self._day(NAMES)
        b = self._day(None)
        self.assertEqual([(s["category"], s["start"], s["end"]) for s in a["spans"]],
                         [(s["category"], s["start"], s["end"]) for s in b["spans"]])
        self.assertEqual(a["summary"]["by_category"], b["summary"]["by_category"])


class CommonNamesTest(unittest.TestCase):
    def test_normalize_defaults_and_drops_bad_entries(self):
        n = common.normalize_names({
            "browsers": ["Chrome.exe", "", None],
            "strip": ["a", 3],
            "title": [{"pattern": "X", "name": "x"}, {"pattern": "", "name": "no"}, "junk",
                      {"pattern": "Y", "name": "y", "is_regex": True, "exe": "Foo.EXE"}],
            "exe": {"Foo.EXE": "Foo", "": "bad", "Bar.exe": ""},
        })
        self.assertEqual(n["browsers"], ["chrome.exe"])
        self.assertEqual(n["strip"], ["a"])
        self.assertEqual(n["title"], [{"pattern": "X", "name": "x", "is_regex": False},
                                      {"pattern": "Y", "name": "y", "is_regex": True, "exe": "foo.exe"}])
        self.assertEqual(n["exe"], {"foo.exe": "Foo"})
        self.assertEqual(common.normalize_names(None), {"browsers": [], "strip": [], "title": [], "exe": {}})

    def test_load_names_copies_default_and_survives_broken_json(self):
        tmp = tempfile.mkdtemp(prefix="maeng_names_")
        old_path, old_default = common.NAMES_PATH, common.NAMES_DEFAULT_PATH
        try:
            common.NAMES_PATH = os.path.join(tmp, "names.json")
            common.NAMES_DEFAULT_PATH = old_default
            n = common.load_names()
            self.assertTrue(os.path.exists(common.NAMES_PATH))
            self.assertIn("chrome.exe", n["browsers"])
            self.assertEqual(n["exe"].get("powerpnt.exe"), "PowerPoint")
            with open(common.NAMES_PATH, "w", encoding="utf-8") as f:
                f.write("{ broken")
            self.assertEqual(common.load_names_raw(), {})
            self.assertEqual(common.load_names(), {"browsers": [], "strip": [], "title": [], "exe": {}})
            self.assertEqual(display_name(CHROME, "ChatGPT - Chrome", common.load_names()), "chrome")
        finally:
            common.NAMES_PATH, common.NAMES_DEFAULT_PATH = old_path, old_default
            shutil.rmtree(tmp, ignore_errors=True)


class NamesRegressionTest(unittest.TestCase):
    """2026-09-06 리뷰 findings 회귀 (Adobe 규칙이 Acrobat/PDF 를 가리던 문제, names.json version 갱신)."""

    def n(self, exe, title):
        return display_name(exe, title, NAMES)

    def test_acrobat_pdf_not_masked_by_adobe(self):
        self.assertEqual(self.n("Acrobat.exe", "paper.pdf - Adobe Acrobat Reader (64-bit)"), "PDF")
        self.assertEqual(self.n("InDesign.exe", "Adobe InDesign 2026"), "InDesign")
        self.assertEqual(self.n(CHROME, "Adobe Creative Cloud - Chrome"), "Adobe")

    def test_new_chat_tab_is_claude(self):
        self.assertEqual(self.n(CHROME, "New chat - Chrome"), "Claude")
        self.assertEqual(self.n(CHROME, "New chat - Claude - Chrome"), "Claude")
        self.assertEqual(self.n(CHROME, "New chatroom rules - Chrome"), "New chatroom rules")

    def test_default_version_refreshes_names_json(self):
        """default 의 version 이 더 높으면 names.json 을 새로 고치고 옛 파일을 .bak 으로 남긴다."""
        import json, os, shutil, tempfile
        tmp = tempfile.mkdtemp()
        old_path, old_default = common.NAMES_PATH, common.NAMES_DEFAULT_PATH
        try:
            common.NAMES_PATH = os.path.join(tmp, "names.json")
            common.NAMES_DEFAULT_PATH = os.path.join(tmp, "names.default.json")
            with open(common.NAMES_PATH, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "title": [{"pattern": "Adobe", "name": "낡은규칙"}]}, f)
            with open(common.NAMES_DEFAULT_PATH, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "title": [{"pattern": "Adobe", "name": "새규칙"}]}, f)
            data = common.load_names_raw()
            self.assertEqual(data["title"][0]["name"], "새규칙")
            self.assertTrue(os.path.exists(common.NAMES_PATH + ".bak"))
            with open(common.NAMES_PATH, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["version"], 2)
        finally:
            common.NAMES_PATH, common.NAMES_DEFAULT_PATH = old_path, old_default
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
