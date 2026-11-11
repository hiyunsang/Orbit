# -*- coding: utf-8 -*-
"""
tests/test_drawer.py — DESIGN §35 서랍: 글·파일 넣기, 복사용 목록, 파일 바이트 응답(Raw), 삭제가 파일도 지움.
메모리 DB + 임시 폴더(server.DRAWER_DIR)로 핸들러를 직접 부른다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import base64
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import server   # noqa: E402

# 1×1 투명 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class DrawerApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        self.tmp = tempfile.mkdtemp(prefix="horarium_drawer_")
        self._old = (server.DRAWER_DIR, server.DRAWER_MAX)
        server.DRAWER_DIR = self.tmp

    def tearDown(self):
        server.DRAWER_DIR, server.DRAWER_MAX = self._old
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- 도우미
    def items(self):
        return server.api_drawer_get(self.conn, {}, None)["items"]

    def add_text(self, value, label=""):
        return server.api_drawer_add(self.conn, {}, {"label": label, "value": value})["id"]

    def add_file(self, name, raw=PNG, label=""):
        return server.api_drawer_add(self.conn, {}, {
            "label": label, "name": name, "data": base64.b64encode(raw).decode("ascii")})["id"]

    def files(self):
        return sorted(os.listdir(self.tmp))

    def assertApiError(self, code, fn, *a):
        with self.assertRaises(server.ApiError) as cm:
            fn(*a)
        self.assertEqual(cm.exception.code, code, cm.exception.msg)

    # --- 글
    def test_text_add_and_default_label(self):
        tid = self.add_text(u"  123-45-67890 ")
        it = self.items()
        self.assertEqual(len(it), 1)
        self.assertEqual(it[0]["id"], tid)
        self.assertEqual(it[0]["kind"], "text")
        self.assertEqual(it[0]["value"], u"123-45-67890", "앞뒤 공백은 자른다")
        self.assertEqual(it[0]["label"], u"123-45-67890", "이름이 비면 내용 앞 20자")
        self.assertIsNone(it[0]["url"])
        self.assertFalse(it[0]["is_image"])

    def test_text_label_kept_and_long_value_truncated_label(self):
        self.add_text(u"가" * 50, label=u"주소")
        self.add_text(u"나" * 50)
        it = self.items()
        self.assertEqual(it[0]["label"], u"주소")
        self.assertEqual(it[1]["label"], u"나" * 20)

    def test_text_empty_rejected(self):
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "x", "value": "   "})
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "x"})
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "x", "value": "a" * (server.DRAWER_TEXT_MAX + 1)})
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "L" * 101, "value": "a"})

    # --- 파일
    def test_file_add_writes_file(self):
        fid = self.add_file(u"사인.png", label=u"내 사인")
        it = self.items()[0]
        self.assertEqual(it["kind"], "file")
        self.assertEqual(it["label"], u"내 사인")
        self.assertEqual(it["value"], u"사인.png", "value 에 원래 이름")
        self.assertEqual(it["mime"], "image/png")
        self.assertEqual(it["size"], len(PNG))
        self.assertTrue(it["is_image"])
        self.assertEqual(it["url"], "/api/drawer/file?id=%d" % fid)
        self.assertEqual(self.files(), [u"%d_사인.png" % fid])
        with open(os.path.join(self.tmp, self.files()[0]), "rb") as f:
            self.assertEqual(f.read(), PNG)

    def test_file_default_label_is_stem(self):
        self.add_file(u"scan 2026.PNG")
        it = self.items()[0]
        self.assertEqual(it["label"], u"scan 2026")
        self.assertEqual(it["mime"], "image/png", "확장자는 소문자로 맞춰 MIME 을 찾는다")

    def test_file_name_sanitized(self):
        fid = self.add_file(u"..\\..\\x\\사인.png")
        self.assertEqual(self.files(), [u"%d_사인.png" % fid], "경로 탈출 금지 — basename 만")
        fid2 = self.add_file(u"../../etc/passwd")
        self.assertEqual(self.items()[1]["mime"], "application/octet-stream")
        self.assertTrue(self.files()[1].startswith(u"%d_" % fid2))
        fid3 = self.add_file(u"a" * 200 + u".pdf")
        name3 = [n for n in self.files() if n.startswith(u"%d_" % fid3)][0]
        self.assertTrue(name3.endswith(u".pdf"))
        self.assertLessEqual(len(name3), 70, "줄기 50자 + 확장자")

    def test_file_bad_base64(self):
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "", "name": "a.png", "data": "data:image/png;base64,AAAA"})
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "", "name": "a.png", "data": "***"})
        self.assertEqual(self.files(), [], "실패하면 파일을 남기지 않는다")
        self.assertEqual(self.items(), [], "실패하면 행도 남지 않는다")

    def test_file_empty_and_too_big(self):
        self.assertApiError(400, server.api_drawer_add, self.conn, {}, {"label": "", "name": "a.png", "data": ""})
        server.DRAWER_MAX = 10
        self.assertApiError(413, server.api_drawer_add, self.conn, {}, {"label": "", "name": "a.png", "data": base64.b64encode(PNG).decode("ascii")})
        self.assertEqual(self.files(), [])

    # --- 목록·고치기·삭제
    def test_order_is_insertion(self):
        a = self.add_text(u"하나")
        b = self.add_file(u"b.png")
        c = self.add_text(u"셋")
        self.assertEqual([i["id"] for i in self.items()], [a, b, c])

    def test_update_label_and_value(self):
        tid = self.add_text(u"옛 값", label=u"옛 이름")
        server.api_drawer_update(self.conn, {}, {"id": tid, "label": u"새 이름", "value": u" 새 값 "})
        it = self.items()[0]
        self.assertEqual((it["label"], it["value"]), (u"새 이름", u"새 값"))
        self.assertApiError(400, server.api_drawer_update, self.conn, {}, {"id": tid, "label": ""})
        self.assertApiError(400, server.api_drawer_update, self.conn, {}, {"id": tid})
        self.assertApiError(404, server.api_drawer_update, self.conn, {}, {"id": 999, "label": "x"})
        fid = self.add_file(u"s.png")
        server.api_drawer_update(self.conn, {}, {"id": fid, "label": u"사인"})
        self.assertEqual(self.items()[1]["label"], u"사인")
        self.assertApiError(400, server.api_drawer_update, self.conn, {}, {"id": fid, "value": "x"})

    def test_delete_removes_file_too(self):
        fid = self.add_file(u"s.png")
        tid = self.add_text(u"t")
        self.assertEqual(len(self.files()), 1)
        server.api_drawer_delete(self.conn, {}, {"id": fid})
        self.assertEqual(self.files(), [])
        self.assertEqual([i["id"] for i in self.items()], [tid])
        server.api_drawer_delete(self.conn, {}, {"id": tid})
        self.assertEqual(self.items(), [])
        self.assertApiError(404, server.api_drawer_delete, self.conn, {}, {"id": tid})

    # --- 파일 응답 (Raw)
    def test_file_endpoint_returns_raw_bytes(self):
        fid = self.add_file(u"사인.png")
        r = server.api_drawer_file(self.conn, {"id": [str(fid)]}, None)
        self.assertIsInstance(r, server.Raw)
        self.assertEqual(r.data, PNG)
        self.assertEqual(r.ctype, "image/png")
        cd = r.headers.get("Content-Disposition", "")
        self.assertTrue(cd.startswith("inline; filename*=UTF-8''"), cd)
        self.assertNotIn(u"사인", cd, "헤더는 ASCII — 퍼센트 인코딩")

    def test_file_endpoint_errors(self):
        self.assertApiError(400, server.api_drawer_file, self.conn, {"id": ["x"]}, None)
        self.assertApiError(404, server.api_drawer_file, self.conn, {"id": ["77"]}, None)
        tid = self.add_text(u"글")
        self.assertApiError(404, server.api_drawer_file, self.conn, {"id": [str(tid)]}, None)
        fid = self.add_file(u"s.png")
        os.remove(os.path.join(self.tmp, self.files()[0]))
        self.assertApiError(404, server.api_drawer_file, self.conn, {"id": [str(fid)]}, None)

    def test_routes_registered(self):
        for m, p in (("GET", "/api/drawer"), ("GET", "/api/drawer/file"), ("POST", "/api/drawer"),
                     ("POST", "/api/drawer/update"), ("POST", "/api/drawer/delete"), ("POST", "/api/drawer/reveal")):
            self.assertIn((m, p), server.ROUTES, "%s %s 가 ROUTES 에 없다" % (m, p))


if __name__ == "__main__":
    unittest.main()
