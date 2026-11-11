# -*- coding: utf-8 -*-
"""tests/test_notes.py — DESIGN §27 메모 판 번호와 덮어쓰기 방지."""
import os
import sys
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import server   # noqa: E402


class NoteVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = common.NOTES_DIR
        common.NOTES_DIR = self.tmp

    def tearDown(self):
        common.NOTES_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, name="pinned"):
        return server.api_note_get(None, {"name": [name]}, None)

    def save(self, text, **kw):
        body = {"name": "pinned", "text": text}
        body.update(kw)
        return server.api_note_save(None, {}, body)

    def test_get_returns_ver_and_empty_note_has_ver(self):
        r = self.get()
        self.assertEqual(r["text"], "")
        self.assertEqual(len(r["ver"]), 12)

    def test_save_with_matching_base(self):
        v0 = self.get()["ver"]
        r = self.save("연구실에서 씀", base=v0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["ver"], self.get()["ver"])
        self.assertNotEqual(r["ver"], v0)

    def test_stale_base_with_different_text_conflicts_and_keeps_file(self):
        v0 = self.get()["ver"]
        self.save("집에서 고침", base=v0)                 # 다른 기기가 먼저 저장
        r = self.save("연구실의 낡은 창에서 씀", base=v0)   # 옛 판을 들고 저장 시도
        self.assertFalse(r["ok"])
        self.assertTrue(r["conflict"])
        self.assertEqual(r["text"], "집에서 고침")
        self.assertEqual(self.get()["text"], "집에서 고침", "충돌이면 파일을 건드리지 않는다 (§27.2)")
        self.assertEqual(r["ver"], self.get()["ver"])

    def test_stale_base_but_same_text_is_ok(self):
        v0 = self.get()["ver"]
        self.save("같은 글", base=v0)
        r = self.save("같은 글", base=v0)
        self.assertTrue(r["ok"])

    def test_no_base_saves_like_before(self):
        self.save("처음")
        r = self.save("덮어씀")
        self.assertTrue(r["ok"])
        self.assertEqual(self.get()["text"], "덮어씀")

    def test_append_on_conflict_with_tail(self):
        v0 = self.get()["ver"]
        self.save("집에서 고침\n", base=v0)
        r = self.save("\n연구실 한 줄", base=v0, on_conflict="append", tail="연구실 한 줄")
        self.assertTrue(r["ok"])
        self.assertEqual(self.get()["text"], "집에서 고침\n연구실 한 줄")

    def test_append_on_conflict_without_tail_keeps_both(self):
        self.save("원래 글")
        v1 = self.get()["ver"]
        self.save("집에서 통째로 바꿈", base=v1)
        self.save("연구실에서 통째로 바꿈", base=v1, on_conflict="append")
        t = self.get()["text"]
        self.assertIn("집에서 통째로 바꿈", t)
        self.assertIn("연구실에서 통째로 바꿈", t)
        self.assertLess(t.index("집에서"), t.index("연구실에서"))

    def test_dated_note_roundtrip(self):
        server.api_note_save(None, {}, {"name": "2026-10-05", "text": "오늘 메모"})
        r = server.api_note_get(None, {"name": ["2026-10-05"]}, None)
        self.assertEqual(r["text"], "오늘 메모")


if __name__ == "__main__":
    unittest.main()
