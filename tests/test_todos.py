# -*- coding: utf-8 -*-
"""
tests/test_todos.py — DESIGN §20 할 일 3상태(대기/진행/완료)와 이월 체크.
server 의 할 일 핸들러를 메모리 DB 로 직접 호출한다.

실행:
  python -m unittest discover -s tests -t . -v   (작업 디렉터리 = MAENG_worklog)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import server   # noqa: E402


class TodoApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def add(self, text, day="2026-09-22"):
        return server.api_todos_add(self.conn, {}, {"text": text, "day": day})["id"]

    def get(self, date="2026-09-22"):
        return server.api_todos_get(self.conn, {"date": [date]}, None)

    def update(self, **body):
        return server.api_todos_update(self.conn, {}, body)

    def progress_rows(self):
        return self.conn.execute("SELECT COUNT(*) FROM todo_progress").fetchone()[0]

    # ---- 진행 상태
    def test_new_todo_is_not_doing(self):
        self.add("a")
        t = self.get()["today"][0]
        self.assertEqual(t["doing"], 0)
        self.assertEqual(t["done"], 0)

    def test_doing_toggle(self):
        tid = self.add("a")
        self.update(id=tid, doing=1)
        self.assertEqual(self.get()["today"][0]["doing"], 1)
        self.assertEqual(self.progress_rows(), 1)
        self.update(id=tid, doing=1)   # 두 번 눌러도 행 하나 (INSERT OR IGNORE)
        self.assertEqual(self.progress_rows(), 1)
        self.update(id=tid, doing=0)
        self.assertEqual(self.get()["today"][0]["doing"], 0)
        self.assertEqual(self.progress_rows(), 0)

    def test_done_clears_doing(self):
        tid = self.add("a")
        self.update(id=tid, doing=1)
        self.update(id=tid, done=1)
        t = self.get()["today"][0]
        self.assertEqual((t["done"], t["doing"]), (1, 0), "완료하면 진행은 자동으로 풀린다 (§20.2)")
        self.assertEqual(self.progress_rows(), 0)

    def test_done_and_doing_in_one_request_done_wins(self):
        tid = self.add("a")
        self.update(id=tid, done=1, doing=1)
        self.assertEqual(self.progress_rows(), 0)

    def test_undone_stays_not_doing(self):
        tid = self.add("a")
        self.update(id=tid, done=1)
        self.update(id=tid, done=0)
        self.assertEqual(self.get()["today"][0]["doing"], 0)

    def test_delete_removes_progress_row(self):
        tid = self.add("a")
        self.update(id=tid, doing=1)
        server.api_todos_delete(self.conn, {}, {"id": tid})
        self.assertEqual(self.progress_rows(), 0, "삭제하면 진행 행도 지운다 (§20.3)")

    # ---- 정렬: 진행 → 대기 → 완료
    def test_today_order_doing_first_done_last(self):
        a = self.add("대기")
        b = self.add("완료")
        c = self.add("진행")
        self.update(id=b, done=1)
        self.update(id=c, doing=1)
        self.assertEqual([t["text"] for t in self.get()["today"]], ["진행", "대기", "완료"])

    # ---- 이월
    def test_carried_check_moves_day(self):
        tid = self.add("어제 것", day="2026-09-21")
        r = self.get("2026-09-22")
        self.assertEqual([t["id"] for t in r["carried"]], [tid])
        self.update(id=tid, done=1, day="2026-09-22")   # 클라이언트가 보내는 이월 체크 요청 그대로
        r = self.get("2026-09-22")
        self.assertEqual(r["carried"], [])
        self.assertEqual([(t["id"], t["done"]) for t in r["today"]], [(tid, 1)],
                         "이월된 할 일을 끝내면 보고 있는 날짜의 완료 목록에 남는다 (§20.2)")
        self.assertEqual(self.get("2026-09-21")["today"], [], "원래 날짜 목록에서는 빠진다")

    def test_carried_doing_shows_in_carried(self):
        tid = self.add("어제 것", day="2026-09-21")
        self.update(id=tid, doing=1)
        self.assertEqual(self.get("2026-09-22")["carried"][0]["doing"], 1)

    def test_past_day_keeps_done_items(self):
        tid = self.add("한 일", day="2026-09-20")
        self.update(id=tid, done=1)
        self.assertEqual([t["done"] for t in self.get("2026-09-20")["today"]], [1],
                         "과거 날짜에는 그 날 끝낸 것이 그대로 남는다 = 일자별 기록 (§20.2)")


if __name__ == "__main__":
    unittest.main()
