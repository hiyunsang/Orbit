# -*- coding: utf-8 -*-
"""
tests/test_devices.py — DESIGN §36 다른 기기에서 쓰기: 안/밖 판정, 비밀번호·로그인·세션, 문지기(gate), 테일넷 주소 읽기.
설정 파일은 임시 폴더(server.DEVICES_PATH)로 돌리고, 문지기는 소켓 없이 헤더 사전으로 부른다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server   # noqa: E402

PW = "test-pass-1234"           # 테스트 전용 값 (실제 설정 파일에는 쓰지 않는다 — DEVICES_PATH 가 임시 폴더)
LOCAL = {"Host": "127.0.0.1:8771"}
TAILNET = {"Host": "pc.example.ts.net:8771", "X-Forwarded-For": "100.87.172.61", "X-Forwarded-Proto": "https",
           "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"}


class DevicesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="horarium_dev_")
        self._old = (server.DEVICES_PATH, server.LOGIN_FAIL_DELAY)
        server.DEVICES_PATH = os.path.join(self.tmp, "devices.json")
        server.LOGIN_FAIL_DELAY = 0
        server._DEV_CACHE.update(mt=-1, cfg={}, bad_t=0)
        server._LOGIN_FAILS[:] = []
        server._SESS_SEEN.clear()

    def tearDown(self):
        server.DEVICES_PATH, server.LOGIN_FAIL_DELAY = self._old
        server._DEV_CACHE.update(mt=-1, cfg={}, bad_t=0)
        server._LOGIN_FAILS[:] = []
        shutil.rmtree(self.tmp, ignore_errors=True)

    def turn_on(self):
        self.assertEqual(server.devices_set_password(PW), {"ok": True})
        server._remote_change(lambda r: r.update(on=True))

    def login(self, headers=TAILNET, pw=PW):
        return server.devices_login(headers, "127.0.0.1", {"password": pw})

    def cookie_of(self, set_cookie):
        return {"Cookie": set_cookie.split(";")[0]}

    def gate(self, headers, method="GET", path="/", peer="127.0.0.1"):
        return server.gate(headers, peer, method, path)


class LocalTest(DevicesBase):
    def test_local(self):
        self.assertTrue(server.is_local_request({"Host": "127.0.0.1:8771"}, "127.0.0.1"))
        self.assertTrue(server.is_local_request({"Host": "localhost:8771"}, "::1"))
        self.assertTrue(server.is_local_request({}, "127.0.0.1"), "Host 가 없는 서버끼리 요청(urllib)")

    def test_remote(self):
        self.assertFalse(server.is_local_request({"Host": "127.0.0.1:8771"}, "100.87.172.61"), "상대가 이 PC 가 아니면 밖")
        self.assertFalse(server.is_local_request({"Host": "pc.example.ts.net:8771"}, "127.0.0.1"), "Host 가 테일넷 이름이면 밖")
        self.assertFalse(server.is_local_request({"Host": "127.0.0.1:8771", "X-Forwarded-For": "100.1.2.3"}, "127.0.0.1"),
                         "넘겨 준 흔적이 있으면 밖")
        self.assertFalse(server.is_local_request({"Host": "127.0.0.1:8771", "Tailscale-User-Login": "a@b"}, "127.0.0.1"))


class LoginTest(DevicesBase):
    def test_password_rules(self):
        self.assertIn("error", server.devices_set_password("short"))
        self.assertFalse(os.path.exists(server.DEVICES_PATH), "짧은 비밀번호는 아무것도 쓰지 않는다")
        self.assertEqual(server.devices_set_password(PW), {"ok": True})
        with open(server.DEVICES_PATH, encoding="utf-8") as f:
            d = json.load(f)
        r = d["remote"]
        self.assertNotIn(PW, json.dumps(d), "비밀번호 원문은 저장하지 않는다")
        self.assertEqual(r["iter"], 200000)
        self.assertEqual(r["hash"], server._pw_hash(PW, r["salt"]))

    def test_login_off(self):
        code, out, cookie = self.login()
        self.assertEqual(code, 403)
        server.devices_set_password(PW)
        code, out, cookie = self.login()
        self.assertEqual(code, 403, "비밀번호만 있고 꺼져 있으면 403")
        self.assertIsNone(cookie)

    def test_login_ok_sets_secure_cookie_and_session(self):
        self.turn_on()
        code, out, cookie = self.login()
        self.assertEqual((code, out), (200, {"ok": True}))
        self.assertTrue(cookie.startswith("hor_s="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Max-Age=%d" % (60 * 86400), cookie)
        self.assertIn("; Secure", cookie, "HTTPS 로 왔으면 Secure")
        tok = server._cookie_token(self.cookie_of(cookie))
        self.assertTrue(server._session_ok(tok))
        v = server.devices_view(False)
        self.assertEqual(v["sessions"][0]["dev"], "iPhone · Safari")

    def test_cookie_not_secure_over_http(self):
        self.turn_on()
        h = dict(TAILNET)
        h["X-Forwarded-Proto"] = "http"
        code, out, cookie = self.login(h)
        self.assertEqual(code, 200)
        self.assertNotIn("Secure", cookie)

    def test_wrong_password_and_lockout(self):
        self.turn_on()
        for _i in range(8):
            code, out, cookie = self.login(pw="wrong-password")
            self.assertEqual(code, 401)
            self.assertIsNone(cookie)
        code, out, cookie = self.login()
        self.assertEqual(code, 429, "10분에 8번 틀리면 맞는 비밀번호도 잠시 막는다")

    def test_session_expiry_and_logout(self):
        self.turn_on()
        code, out, cookie = self.login()
        h = self.cookie_of(cookie)
        tok = server._cookie_token(h)
        self.assertFalse(server._session_ok(tok, now=time.time() + 61 * 86400), "60일 지나면 풀린다")
        self.assertTrue(server._session_ok(tok))
        server.devices_logout(h)
        self.assertFalse(server._session_ok(tok))

    def test_password_change_logs_everyone_out(self):
        self.turn_on()
        code, out, cookie = self.login()
        tok = server._cookie_token(self.cookie_of(cookie))
        server.devices_set_password("another-pass-5678")
        self.assertFalse(server._session_ok(tok))

    def test_sessions_capped(self):
        self.turn_on()
        for _i in range(server.SESSION_MAX + 3):
            self.assertEqual(self.login()[0], 200)
        self.assertEqual(len(server.remote_cfg()["sessions"]), server.SESSION_MAX)

    def test_broken_file_is_closed_and_recovered(self):
        with open(server.DEVICES_PATH, "w", encoding="utf-8") as f:
            f.write("{ not json")
        server._DEV_CACHE.update(mt=-1, cfg={}, bad_t=0)
        self.assertEqual(server.remote_cfg(), {}, "못 읽으면 닫힌 쪽")
        self.assertEqual(self.gate(TAILNET)[0], 403)
        self.assertEqual(server.devices_set_password(PW), {"ok": True}, "고칠 때는 깨진 파일을 옆에 남기고 새로 시작")
        self.assertTrue(any(n.startswith("devices.json.broken_") for n in os.listdir(self.tmp)))


class GateTest(DevicesBase):
    def test_local_passes_without_login(self):
        self.assertIsNone(self.gate(LOCAL))
        self.assertIsNone(self.gate(LOCAL, "POST", "/api/restart"))
        self.assertIsNone(self.gate({}, "POST", "/api/events"), "Janua 처럼 서버끼리 보내는 요청")

    def test_remote_off(self):
        code, data, ctype, extra = self.gate(TAILNET, "GET", "/")
        self.assertEqual(code, 403)
        self.assertIn("text/html", ctype)
        self.assertIn(u"꺼져 있습니다", data.decode("utf-8"))
        code, data, ctype, extra = self.gate(TAILNET, "GET", "/api/day")
        self.assertEqual(code, 403)
        self.assertIn("application/json", ctype)
        self.assertEqual(self.gate(TAILNET, "GET", "/login")[0], 403, "꺼져 있으면 로그인 화면도 없다")

    def test_open_paths_before_login(self):
        self.turn_on()
        for p in ("/login", "/static/manifest.json", "/static/icon-180.png", "/static/logo-mark.svg", "/static/PretendardVariable.woff2"):
            self.assertIsNone(self.gate(TAILNET, "GET", p), p)
        self.assertIsNone(self.gate(TAILNET, "POST", "/api/login"))

    def test_needs_login(self):
        self.turn_on()
        code, data, ctype, extra = self.gate(TAILNET, "GET", "/?mock=1")
        self.assertEqual(code, 302)
        self.assertEqual(extra["Location"], "/login?next=%2F%3Fmock%3D1")
        self.assertEqual(self.gate(TAILNET, "GET", "/static/app.js")[0], 302, "앱 코드는 로그인 뒤에")
        code, data, ctype, extra = self.gate(TAILNET, "GET", "/api/todos")
        self.assertEqual(code, 401)
        self.assertEqual(json.loads(data.decode("utf-8")), {"error": u"로그인이 필요합니다", "login": True})
        self.assertEqual(self.gate(TAILNET, "POST", "/api/todos")[0], 401)
        bad = dict(TAILNET, Cookie="hor_s=" + "x" * 43)
        self.assertEqual(self.gate(bad, "GET", "/api/todos")[0], 401, "모르는 토큰")

    def test_logged_in_passes_but_local_only_posts_blocked(self):
        self.turn_on()
        h = dict(TAILNET, **self.cookie_of(self.login()[2]))
        self.assertIsNone(self.gate(h, "GET", "/"))
        self.assertIsNone(self.gate(h, "GET", "/api/devices"))
        self.assertIsNone(self.gate(h, "POST", "/api/todos"))
        for p in ("/api/devices", "/api/restart", "/api/drawer/reveal"):
            code, data, ctype, extra = self.gate(h, "POST", p)
            self.assertEqual(code, 403, p)
            self.assertIn(u"PC 에서만", data.decode("utf-8"))

    def test_origin_check(self):
        self.assertEqual(self.gate(dict(LOCAL, Origin="http://evil.example"), "POST", "/api/todos")[0], 403,
                         "다른 사이트의 화면이 보낸 POST 는 이 PC 안에서도 거절")
        self.assertEqual(self.gate(dict(LOCAL, Origin="null"), "POST", "/api/todos")[0], 403)
        self.assertIsNone(self.gate(dict(LOCAL, Origin="http://127.0.0.1:8771"), "POST", "/api/todos"))
        self.assertIsNone(self.gate(dict(LOCAL, Origin="http://evil.example"), "GET", "/api/todos"), "GET 은 Origin 을 보지 않는다")
        self.turn_on()
        h = dict(TAILNET, **self.cookie_of(self.login()[2]))
        self.assertIsNone(self.gate(dict(h, Origin="https://pc.example.ts.net:8771"), "POST", "/api/todos"))
        fwd = dict(h, Host="127.0.0.1:8771", **{"X-Forwarded-Host": "pc.example.ts.net:8771", "Origin": "https://pc.example.ts.net:8771"})
        self.assertIsNone(self.gate(fwd, "POST", "/api/todos"), "중계가 Host 를 바꿔도 X-Forwarded-Host 로 맞춘다")
        self.assertEqual(self.gate(dict(h, Origin="https://other.example.ts.net:8771"), "POST", "/api/todos")[0], 403)


class ViewTest(DevicesBase):
    def test_remote_view_hides_devices_and_addresses(self):
        self.turn_on()
        self.login()
        v = server.devices_view(True)
        self.assertEqual(v["remote"], True)
        self.assertNotIn("sessions", v)
        self.assertNotIn("tailscale", v)

    def test_admin_post_ops(self):
        r = server.api_devices_post(None, {}, {"op": "on", "on": True})
        self.assertEqual(r["result"], {"error": u"먼저 비밀번호를 정해 주세요"})
        self.assertFalse(r["on"])
        r = server.api_devices_post(None, {}, {"op": "password", "password": PW})
        self.assertEqual(r["result"], {"ok": True})
        self.assertTrue(r["has_password"])
        r = server.api_devices_post(None, {}, {"op": "on", "on": True})
        self.assertTrue(r["on"])
        self.login()
        r = server.api_devices_post(None, {}, {"op": "logout_all"})
        self.assertEqual(r["sessions"], [])
        with self.assertRaises(server.ApiError):
            server.api_devices_post(None, {}, {"op": "nope"})

    def test_admin_post_refused_when_remote(self):
        server._TL.remote = True
        try:
            with self.assertRaises(server.ApiError) as cm:
                server.api_devices_post(None, {}, {"op": "logout_all"})
            self.assertEqual(cm.exception.code, 403)
        finally:
            server._TL.remote = False

    def test_serve_urls(self):
        status = ("https://desktop-b0hbmgl.tail334ddb.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8790\n\n"
                  "https://desktop-b0hbmgl.tail334ddb.ts.net:8771 (tailnet only)\n|-- / proxy http://127.0.0.1:8771\n\n"
                  "http://desktop-b0hbmgl:8770 (tailnet only)\nhttp://desktop-b0hbmgl.tail334ddb.ts.net:8770 (tailnet only)\n|-- / proxy http://127.0.0.1:8770\n")
        self.assertEqual(server.serve_urls(status, 8771), ["https://desktop-b0hbmgl.tail334ddb.ts.net:8771"])
        self.assertEqual(server.serve_urls(status, 8770), ["http://desktop-b0hbmgl:8770", "http://desktop-b0hbmgl.tail334ddb.ts.net:8770"],
                         "http 는 짧은 이름 먼저")
        self.assertEqual(server.serve_urls("", 8771), [])

    def test_ua_short(self):
        self.assertEqual(server._ua_short(TAILNET["User-Agent"]), "iPhone · Safari")
        self.assertEqual(server._ua_short("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130 Safari/537.36 Edg/130"), "Windows · Edge")
        self.assertEqual(server._ua_short("Mozilla/5.0 (iPhone) CriOS/130 Mobile Safari/604.1"), "iPhone · Chrome")

    def test_routes(self):
        self.assertIn(("GET", "/api/devices"), server.ROUTES)
        self.assertIn(("POST", "/api/devices"), server.ROUTES)


if __name__ == "__main__":
    unittest.main()
