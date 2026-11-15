# -*- coding: utf-8 -*-
"""tests/test_inbox.py — DESIGN §51 받아 적기 · 개인 비서. claude 는 가짜 함수(inbox.classifier)로 바꾼다."""
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import common   # noqa: E402
import inbox    # noqa: E402
import plans    # noqa: E402
import server   # noqa: E402

TODAY = "2026-11-15"   # 일요일


def T(typ, title, **kw):
    d = {"type": typ, "title": title, "day": None, "time": None, "repeat": "none", "area": "연구",
         "doing": False, "someday": False, "ref": None, "needs_lookup": False, "lookup_query": ""}
    d.update(kw)
    return d


class PureTest(unittest.TestCase):
    def test_extract_json(self):
        self.assertIsNone(inbox.extract_json(None))
        self.assertIsNone(inbox.extract_json("모르겠습니다"))
        got = inbox.extract_json('설명 먼저\n```json\n{"items": [{"type": "task", "title": "a", "x": {"y": 1}}]}\n```\n끝')
        self.assertEqual(got["items"][0]["title"], "a")
        # 앞에 다른 JSON 이 있어도 items 가 있는 마지막 것
        got = inbox.extract_json('{"foo": 1} 그리고 {"items": [{"type": "note", "title": "b"}]}')
        self.assertEqual(got["items"][0]["type"], "note")

    def test_normalize(self):
        items = inbox.normalize({"items": [
            {"type": "weird", "title": "  NX   설계 "},                 # 모르는 종류 → 할 일, 공백 정리
            {"type": "done", "title": "실험 끝냄", "ref": "zz"},        # 가리킬 것이 없는 '끝냄' → 기억(기록)
            {"type": "same", "title": "레니쇼 발주", "ref": None},      # 가리킬 것이 없는 '같음' → 새 할 일
            {"type": "task", "title": ""},                              # 빈 것은 버림
            {"type": "done", "ref": "t12"},                             # ref 만 있는 끝냄은 그대로
            {"type": "deadline", "title": "초록", "needs_lookup": True, "lookup_query": "2027 초록 마감"},
            {"type": "deadline", "title": "숙제", "day": "2026-11-20", "needs_lookup": True, "lookup_query": "x"},
            {"type": "task", "title": "t", "day": "2026-13-40", "time": "9시", "doing": "yes"},
            "문자열",
        ]})
        self.assertEqual([i["type"] for i in items], ["task", "note", "task", "done", "deadline", "deadline", "task"])
        self.assertEqual(items[0]["title"], "NX 설계")
        self.assertEqual(items[3]["ref"], "t12")
        self.assertTrue(items[4]["needs_lookup"])
        self.assertFalse(items[5]["needs_lookup"], "날짜가 있으면 찾지 않는다")
        self.assertEqual((items[6]["day"], items[6]["time"], items[6]["doing"]), (None, None, False), "이상한 값은 버린다")
        self.assertEqual(inbox.normalize(None), [])

    def test_prompt_has_context_and_rules(self):
        p = inbox.prompt("레니쇼 발주함", TODAY, ["t3 레니쇼 리니어스케일 발주 · 할 일 · 하는 중"])
        self.assertIn("2026-11-15(일요일)", p)
        self.assertIn("t3 레니쇼 리니어스케일 발주", p)
        self.assertIn("[글]\n레니쇼 발주함", p)
        for w in ("done", "same", "note", "날짜를 지어내지 않는다", "하라고 하심"):
            self.assertIn(w, p)


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.conn = common.connect(path=":memory:")
        common.init_schema(self.conn)
        self._saved = (inbox.ASYNC, inbox.classifier, plans.ASYNC, plans.parser, plans.looker, plans._find_claude)
        inbox.ASYNC = False
        plans.ASYNC = False
        self.answer = {}
        self.seen_ctx = []

        def fake(text, today, ctx):
            self.seen_ctx.append(ctx)
            a = self.answer.get(text)
            if isinstance(a, Exception):
                raise a
            return a
        inbox.classifier = fake
        plans.looker = lambda q, today: {"day": "2027-01-29", "confidence": "confirmed", "source_url": "https://kspe.or.kr/x", "note": "공지"}
        self._today = common.today_str
        common.today_str = lambda cfg=None, now=None: TODAY

    def tearDown(self):
        (inbox.ASYNC, inbox.classifier, plans.ASYNC, plans.parser, plans.looker, plans._find_claude) = self._saved
        common.today_str = self._today
        self.conn.close()

    # 돕는 것
    def put(self, text, items, day=TODAY):
        self.answer[text] = {"items": items} if items is not None else None
        return server.api_inbox_add(self.conn, {}, {"text": text, "day": day})

    def todos(self, date=TODAY):
        r = server.api_todos_get(self.conn, {"date": [date]}, None)
        return r["today"] + r["carried"]

    def todo(self, text, day="2026-11-01", doing=False):
        tid = server.api_todos_add(self.conn, {}, {"text": text, "day": day})["id"]
        if doing:
            server.api_todos_update(self.conn, {}, {"id": tid, "doing": 1})
        return tid

    def recent(self):
        return server.api_inbox_get(self.conn, {}, None)["items"]

    # 나누기
    def test_split_into_places(self):
        r = self.put("다 적기", [
            T("task", "레니쇼 견적 교수님께 전달", area="연구"),
            T("task", "NX 도면 출력", doing=True),
            T("task", "공구함 정리", someday=True),
            T("event", "랩미팅", day="2026-11-17", time="15:00"),
            T("deadline", "토플 숙제", day="2026-11-20", area="토플"),
            T("event", "해커스 수업", day="2026-11-21", repeat="weekly"),
            T("note", "벡호프 담당자 김민수 010-1234-5678"),
        ])
        self.assertTrue(r["parsing"])
        tds = dict((x["text"], x) for x in self.todos())
        self.assertEqual(set(tds), {"레니쇼 견적 교수님께 전달", "NX 도면 출력", "공구함 정리"})
        self.assertEqual(tds["레니쇼 견적 교수님께 전달"]["day"], TODAY, "적은 날의 할 일")
        self.assertEqual(tds["레니쇼 견적 교수님께 전달"]["area"], "연구")
        self.assertEqual(tds["NX 도면 출력"]["doing"], 1)
        self.assertEqual(tds["공구함 정리"]["someday"], 1)
        ev = dict((x["title"], x) for x in server.api_events_get(self.conn, {"date": [TODAY]}, None)["items"])
        self.assertEqual((ev["랩미팅"]["kind"], ev["랩미팅"]["day"], ev["랩미팅"]["time"], ev["랩미팅"]["status"]), ("event", "2026-11-17", "15:00", "ready"))
        self.assertEqual((ev["토플 숙제"]["kind"], ev["토플 숙제"]["area"]), ("deadline", "토플"))
        self.assertEqual(ev["해커스 수업"]["repeat"], "weekly")
        ks = server.api_keeps_get(self.conn, {}, None)["items"]
        self.assertEqual([k["text"] for k in ks], ["벡호프 담당자 김민수 010-1234-5678"])
        rec = self.recent()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["status"], "done")
        labels = [x["label"] for x in rec[0]["result"]]
        self.assertEqual(labels, ["할 일", "하는 중", "나중에", "일정 11/17(화)", "마감 11/20(금)", "일정 11/21(토)", "기억"])
        self.assertNotIn("prev", json.dumps(rec[0]["result"]))

    def test_context_given_to_claude(self):
        tid = self.todo("NX 설계 마무리", doing=True)
        server.api_events_add(self.conn, {}, {"text": "광공학 과제", "day": "2026-12-06"})
        inbox.keep_add(self.conn, "그래프 단위 mm")
        self.put("아무거나", [T("task", "x")])
        ctx = "\n".join(self.seen_ctx[-1])
        self.assertIn("t%d NX 설계 마무리 · 할 일 · 하는 중" % tid, ctx)
        self.assertIn("광공학 과제 · 할 일 12/6(일)", ctx)
        self.assertIn("그래프 단위 mm · 기억", ctx)

    def test_done_and_undo_todo(self):
        tid = self.todo("NX 설계 마무리", day="2026-11-02", doing=True)
        self.put("NX 끝냄, 도면 출력해서 가공업체 보내기", [T("done", "", ref="t%d" % tid), T("task", "도면 출력해서 가공업체 보내기")])
        row = self.conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
        self.assertEqual((row["done"], row["day"]), (1, TODAY), "전에 적은 할 일을 끝내면 오늘 한 일로(§20)")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM todo_progress WHERE todo_id=?", (tid,)).fetchone())
        rec = self.recent()[0]
        self.assertEqual([x["label"] for x in rec["result"]], ["끝냄", "할 일"])
        self.assertEqual(rec["result"][0]["title"], "NX 설계 마무리")
        text = server.api_inbox_undo(self.conn, {}, {"id": rec["id"]})["text"]
        self.assertEqual(text, "NX 끝냄, 도면 출력해서 가공업체 보내기")
        row = self.conn.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
        self.assertEqual((row["done"], row["day"]), (0, "2026-11-02"))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM todo_progress WHERE todo_id=?", (tid,)).fetchone(), "하는 중도 돌아온다")
        self.assertEqual([x["text"] for x in self.todos()], ["NX 설계 마무리"], "만든 할 일은 지워진다")
        self.assertEqual(self.recent(), [], "되돌린 글은 화면에서 사라진다")
        with self.assertRaises(server.ApiError):
            server.api_inbox_undo(self.conn, {}, {"id": rec["id"]})      # 두 번은 안 된다

    def test_done_weekly_plan_and_undo(self):
        eid = server.api_events_add(self.conn, {}, {"text": "토플 수업", "day": "2026-11-08"})["id"]
        self.conn.execute("UPDATE plans SET repeat='weekly' WHERE id=?", (eid,))
        self.conn.commit()
        self.put("오늘 토플 수업 들음", [T("done", "", ref="p%d" % eid)])
        row = self.conn.execute("SELECT * FROM plans WHERE id=?", (eid,)).fetchone()
        self.assertEqual((row["done"], row["skip_until"]), (0, "2026-11-16"), "매주 일정은 이번 것만 끝")
        server.api_inbox_undo(self.conn, {}, {"id": self.recent()[0]["id"]})
        self.assertIsNone(self.conn.execute("SELECT skip_until FROM plans WHERE id=?", (eid,)).fetchone()[0])

    def test_same_creates_nothing(self):
        tid = self.todo("레니쇼 리니어스케일 발주")
        self.put("레니쇼 리니어스케일 발주", [T("same", "", ref="t%d" % tid)])
        self.assertEqual(len(self.todos()), 1)
        self.assertEqual(self.recent()[0]["result"][0]["label"], "이미 있음")

    def test_done_on_missing_or_closed_ref_is_kept(self):
        self.put("실험 3 끝냄", [T("done", "실험 3 끝냄", ref="t999")])
        self.assertEqual([k["text"] for k in inbox.keeps(self.conn)], ["실험 3 끝냄"], "가리킬 것이 없으면 기록으로 남긴다")

    def test_failures_keep_the_text(self):
        self.put("이상한 답", None)                                    # claude 가 답을 못 함
        self.answer["터짐"] = RuntimeError("boom")
        server.api_inbox_add(self.conn, {}, {"text": "터짐", "day": TODAY})
        self.put("빈 답", [])
        texts = sorted(x["text"] for x in self.todos())
        self.assertEqual(texts, ["빈 답", "이상한 답", "터짐"])
        self.assertEqual(set(x["status"] for x in self.recent()), {"failed"})

    def test_stale_parsing_is_rescued(self):
        now = time.time()
        self.conn.execute("INSERT INTO inbox(text, day, status, created_ts, updated_ts) VALUES(?,?,?,?,?)",
                          ("서버가 꺼졌던 글", TODAY, "parsing", now - 3600, now - 3600))
        self.conn.execute("INSERT INTO inbox(text, day, status, created_ts, updated_ts) VALUES(?,?,?,?,?)",
                          ("방금 적은 글", TODAY, "parsing", now, now))
        self.conn.commit()
        rec = dict((x["text"], x) for x in self.recent())
        self.assertEqual(rec["서버가 꺼졌던 글"]["status"], "failed")
        self.assertEqual(rec["방금 적은 글"]["status"], "parsing", "아직 나누는 중인 것은 그대로")
        self.assertEqual([x["text"] for x in self.todos()], ["서버가 꺼졌던 글"])

    def test_dismiss_and_show_window(self):
        self.put("a", [T("task", "a")])
        self.put("b", [T("task", "b")])
        ids = [x["id"] for x in self.recent()]
        server.api_inbox_dismiss(self.conn, {}, {"id": ids[0]})
        self.assertEqual([x["text"] for x in self.recent()], ["b"])
        self.conn.execute("UPDATE inbox SET updated_ts=?", (time.time() - inbox.SHOW_SEC - 5,))
        self.conn.commit()
        self.assertEqual(self.recent(), [], "10분이 지나면 화면에서 내려간다")

    def test_lookup_runs_after_commit(self):
        self.put("정밀공학회 2027 춘계 초록", [T("deadline", "정밀공학회 2027 춘계 초록 마감", needs_lookup=True, lookup_query="2027 한국정밀공학회 춘계 초록 마감")])
        it = server.api_events_get(self.conn, {"date": [TODAY]}, None)["items"][0]
        self.assertEqual((it["day"], it["confidence"], it["source_url"], it["status"]), ("2027-01-29", "confirmed", "https://kspe.or.kr/x", "ready"))
        self.assertEqual(self.recent()[0]["result"][0]["label"], "마감 · 날짜 미정", "결과 줄은 넣을 때의 모습")

    def test_move_between_todo_and_keep(self):
        self.put("x", [T("task", "벡호프 담당자 번호", area="연구")])
        tid = self.todos()[0]["id"]
        new = server.api_inbox_move(self.conn, {}, {"ref": "t%d" % tid, "to": "note"})["ref"]
        self.assertTrue(new.startswith("k"))
        self.assertEqual(self.todos(), [])
        k = inbox.keeps(self.conn)[0]
        self.assertEqual((k["text"], k["area"]), ("벡호프 담당자 번호", "연구"))
        back = server.api_inbox_move(self.conn, {}, {"ref": new, "to": "task", "day": TODAY})["ref"]
        self.assertTrue(back.startswith("t"))
        self.assertEqual(inbox.keeps(self.conn), [])
        self.assertEqual([(x["text"], x["area"]) for x in self.todos()], [("벡호프 담당자 번호", "연구")])
        with self.assertRaises(server.ApiError):
            server.api_inbox_move(self.conn, {}, {"ref": back, "to": "task"})

    def test_someday_toggle_and_delete_cleans_meta(self):
        tid = self.todo("공구함 정리")
        server.api_todos_update(self.conn, {}, {"id": tid, "someday": 1})
        self.assertEqual(self.todos()[0]["someday"], 1)
        server.api_todos_update(self.conn, {}, {"id": tid, "someday": 0})
        self.assertEqual(self.todos()[0]["someday"], 0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM item_meta WHERE ref=?", ("t%d" % tid,)).fetchone(), "빈 표시는 행째 지운다")
        server.api_todos_update(self.conn, {}, {"id": tid, "someday": 1})
        server.api_todos_delete(self.conn, {}, {"id": tid})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM item_meta").fetchone())

    def test_keeps_api(self):
        kid = server.api_keeps_add(self.conn, {}, {"text": "  EL7041  2개 "})["id"]
        server.api_keeps_update(self.conn, {}, {"id": kid, "text": "EL7041 3개"})
        self.assertEqual(server.api_keeps_get(self.conn, {}, None)["items"][0]["text"], "EL7041 3개")
        server.api_keeps_delete(self.conn, {}, {"id": kid})
        self.assertEqual(server.api_keeps_get(self.conn, {}, None)["items"], [])
        with self.assertRaises(server.ApiError):
            server.api_keeps_add(self.conn, {}, {"text": "  "})

    def test_without_claude_old_split(self):
        inbox.classifier = None
        plans._find_claude = lambda: None
        r = server.api_inbox_add(self.conn, {}, {"text": "레니쇼 발주", "day": TODAY})
        self.assertEqual(r["fallback"], "task")
        r = server.api_inbox_add(self.conn, {}, {"text": "24일 숙제", "day": TODAY})
        self.assertEqual(r["fallback"], "plan")
        self.assertEqual([x["text"] for x in self.todos()], ["레니쇼 발주"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)
        self.assertFalse(server.api_inbox_get(self.conn, {}, None)["claude"])

    def test_empty_text_rejected_and_lines_kept(self):
        with self.assertRaises(server.ApiError):
            server.api_inbox_add(self.conn, {}, {"text": " \n ", "day": TODAY})
        self.put("첫 줄   \n\n\n\n둘째   줄", [T("task", "a")])
        self.assertEqual(self.conn.execute("SELECT text FROM inbox").fetchone()[0], "첫 줄\n\n둘째 줄")


if __name__ == "__main__":
    unittest.main()
