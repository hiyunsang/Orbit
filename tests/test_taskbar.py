# -*- coding: utf-8 -*-
"""DESIGN §46 작업 표시줄 이름표 — 순수 함수만(창을 건드리는 stamp_once 는 실측으로 확인)."""
import io
import json
import os
import shutil
import tempfile
import unittest

import taskbar_id as tb

ATH = {"name": "Athenaeum", "aumid": "MAENG.Athenaeum", "title": "Athenaeum$", "host": "localhost",
       "command": '"C:\\x\\Athenaeum_실행.bat"', "icon": "C:\\x\\athenaeum.ico,0"}


class TaskbarIdTest(unittest.TestCase):
    def setUp(self):
        self.orbit = tb.orbit_app(root="C:\\o", pythonw="C:\\py\\pythonw.exe")
        self.apps = [self.orbit, ATH]

    def test_orbit_app(self):
        self.assertEqual(self.orbit["aumid"], "MAENG.Orbit")
        self.assertEqual(self.orbit["command"], '"C:\\py\\pythonw.exe" "C:\\o\\launch.pyw"', "고정한 단추는 실행기로 다시 연다(서버도 띄우게)")
        self.assertEqual(self.orbit["icon"], "C:\\o\\logo\\orbit-mono.ico,0", "§45 PC 아이콘")

    def test_match_browser_app_window(self):
        chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
        self.assertIs(tb.match(self.apps, u"(3) Orbit · 출근기록", chrome, "Chrome.127.0.0.1_/.browserprole.Default"), self.orbit)
        self.assertIs(tb.match(self.apps, u"Orbit · 출근기록", chrome, "MAENG.Orbit"), self.orbit, "이미 붙인 창도 맞다(값을 새로 고칠 수 있게)")
        self.assertIs(tb.match(self.apps, u"연구 노트 · Athenaeum", edge, "MSEdge.localhost_/"), ATH)
        self.assertIs(tb.match(self.apps, u"Athenaeum", edge, "MSEdge.localhost_/"), ATH)

    def test_match_leaves_other_windows(self):
        chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
        self.assertIsNone(tb.match(self.apps, u"Orbit · 출근기록", chrome, "Chrome"), "보통 크롬 창(탭)은 건드리지 않는다")
        self.assertIsNone(tb.match(self.apps, u"Janua", edge, "MSEdge.localhost_/"), "같은 localhost 의 다른 앱 창")
        self.assertIsNone(tb.match(self.apps, u"Orbit · 출근기록", "C:\\py\\python.exe", "Chrome.127.0.0.1_/"), "브라우저가 아닌 창")
        self.assertIsNone(tb.match(self.apps, u"hiyunsang/Orbit · GitHub", chrome, "Chrome"), "깃허브 탭")
        self.assertIsNone(tb.match(self.apps, u"Orbit · 출근기록", chrome, "Chrome.example.com_/"), "다른 주소의 앱 창")
        self.assertIsNone(tb.match([self.orbit], u"Athenaeum", edge, "MSEdge.localhost_/"), "taskbar_apps.json 이 없으면 Athenaeum 은 모른다")

    def test_wanted_order(self):
        w = tb.wanted(self.orbit)
        self.assertEqual([k for k, _ in w], ["ID", "RelaunchIconResource", "RelaunchCommand", "RelaunchDisplayNameResource"])
        self.assertEqual(dict(w)["RelaunchDisplayNameResource"], "Orbit")

    def test_load_apps(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual([a["name"] for a in tb.load_apps(d, root="C:\\o", pythonw="p")], ["Orbit"], "파일이 없으면 Orbit 만")
            p = os.path.join(d, "taskbar_apps.json")
            with io.open(p, "w", encoding="utf-8") as f:
                json.dump([ATH, {"name": "빈칸"}, dict(ATH, title="(", name="깨진 정규식"), "글자"], f, ensure_ascii=False)
            apps = tb.load_apps(d, root="C:\\o", pythonw="p")
            self.assertEqual([a["name"] for a in apps], ["Orbit", "Athenaeum"], "모자라거나 깨진 항목은 건너뛴다")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(u"{깨진")
            self.assertEqual([a["name"] for a in tb.load_apps(d, root="C:\\o", pythonw="p")], ["Orbit"])
        finally:
            shutil.rmtree(d)

    def test_wired_in(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        server = io.open(os.path.join(root, "server.py"), encoding="utf-8").read()
        launch = io.open(os.path.join(root, "launch.pyw"), encoding="utf-8").read()
        self.assertIn("taskbar_id.start(common.data_dir(CFG))", server, "서버가 2초마다 붙인다")
        self.assertIn("_stamp_soon(ddir)", launch, "실행기는 창을 연 직후 붙인다")
        design = io.open(os.path.join(root, "DESIGN.md"), encoding="utf-8").read()
        self.assertIn("## 46. ", design)


if __name__ == "__main__":
    unittest.main()
