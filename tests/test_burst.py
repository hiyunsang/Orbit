# -*- coding: utf-8 -*-
"""DESIGN §48 폰에서 열 때 요청이 몰리면 502 — 서버 대기열, 프론트의 GET 다시 묻기·토스트 묶기."""
import io
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler

import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class BurstTest(unittest.TestCase):
    def test_queue_size(self):
        self.assertGreaterEqual(server.Server.request_queue_size, 64, "기본 5 면 몰린 연결을 윈도우가 끊는다")
        self.assertLessEqual(server.BIND_RETRY_INTERVAL, 0.1, "재시작 틈을 짧게")

    def test_burst_of_connections_all_answered(self):
        srv = server.Server(("127.0.0.1", 0), _Ok)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05})
        th.daemon = True
        th.start()
        port = srv.server_address[1]
        go = threading.Event()
        bad = []

        def one():
            go.wait()
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=10)
                c.sendall(b"GET / HTTP/1.0\r\n\r\n")
                data = c.recv(64)
                c.close()
                if b" 200 " not in data:
                    bad.append(data)
            except OSError as e:
                bad.append(e)

        ths = [threading.Thread(target=one) for _ in range(100)]
        for t in ths:
            t.start()
        go.set()
        for t in ths:
            t.join(20)
        srv.shutdown()
        srv.server_close()
        self.assertEqual(bad, [], "동시에 100개 — 하나도 끊기지 않는다")

    def test_frontend_retry_and_toast(self):
        js = io.open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
        self.assertIn("var GET_RETRY_MS = [400, 1000, 2500];", js)
        self.assertIn("opt.method === 'GET' ? fetchGet(path, opt, 0) : fetch(path, opt)", js, "POST 는 다시 보내지 않는다")
        self.assertIn("var TOAST_MAX = 3;", js)
        self.assertIn("if (t - sideAt < 2000) return;", js, "focus·visibilitychange 두 번 부르기 막기")
        design = io.open(os.path.join(ROOT, "DESIGN.md"), encoding="utf-8").read()
        self.assertIn("## 48. ", design)


if __name__ == "__main__":
    unittest.main()
