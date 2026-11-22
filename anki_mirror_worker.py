# -*- coding: utf-8 -*-
"""
anki_mirror_worker.py — Orbit 의 Anki 사본을 AnkiWeb 과 맞춘다 (DESIGN §59).

**이 파일은 Orbit 의 파이썬(3.8)이 아니라 이 PC 에 깔린 Anki 의 파이썬으로 돈다** — Anki 꾸러미(anki.collection)의
동기화를 그대로 쓰기 위해서다. `anki_mirror.py` 가 PowerShell 로 Anki 폴더의 python3xx.dll(Py_Main)을 불러 이 파일을 돌린다.

    python anki_mirror_worker.py <사본 폴더> <%APPDATA%> <Anki 폴더>

하는 일: 사본(<사본 폴더>\\collection.anki2 — Orbit 만 쓰는 읽기용 모음, 사용자의 Anki 프로필 모음이 아니다)을 열어
AnkiWeb 과 동기화한다. 미디어는 받지 않는다. 결과는 <사본 폴더>\\result.json 에.

지키는 것:
  - 사용자의 Anki 프로필(%APPDATA%\\Anki2)은 **읽기만** — 저장된 동기화 열쇠(syncKey)와 서버 주소를 읽는다. 쓰지 않는다.
  - 열쇠는 Anki 꾸러미에만 넘긴다. 찍지 않고, 결과 · 상태 파일에 적지 않고, 오류 글에 섞이면 가린다.
  - **절대 올리지 않는다**: 통째로 맞춰야 할 때는 받기(full download)만. 서버가 비어 '올리기' 가 필요하다고 하면 멈춘다.
    사본에는 지킬 변경이 없으므로 받기는 늘 안전하다.

decide() · read_auth() 는 Anki 없이 돈다(Orbit 의 테스트가 3.8 로 부른다).
"""
import io
import json
import os
import pickle
import sqlite3
import sys
import time
import traceback

IO_TIMEOUT_SECS = 60


def decide(required):
    """AnkiWeb 이 말한 '필요한 것'(ChangesRequired 이름) → 사본이 할 일: none · normal · download · stop."""
    return {"NO_CHANGES": "none", "NORMAL_SYNC": "normal",
            "FULL_DOWNLOAD": "download", "FULL_SYNC": "download"}.get(required, "stop")     # FULL_UPLOAD 등은 멈춤


class _Loose(pickle.Unpickler):
    """Anki 프로필은 Qt 물건(창 크기 등)이 섞인 pickle 이다 — Qt 없이 읽도록 그런 것은 None 으로."""

    def find_class(self, module, name):
        if module.startswith(("PyQt", "sip")):
            return lambda *a, **k: None
        return pickle.Unpickler.find_class(self, module, name)


def read_auth(appdata):
    """프로필에서 (열쇠, 서버 주소 또는 None). 로그인한 프로필이 없으면 (None, None). 읽기 전용."""
    db = os.path.join(appdata, "Anki2", "prefs21.db")
    if not os.path.exists(db):
        return None, None
    con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT name, data FROM profiles").fetchall()
    finally:
        con.close()
    for name, data in rows:
        if name == "_global":
            continue
        try:
            d = _Loose(io.BytesIO(data)).load()
        except Exception:
            continue
        if isinstance(d, dict) and d.get("syncKey"):
            return str(d["syncKey"]), (d.get("currentSyncUrl") or d.get("customSyncUrl") or None)
    return None, None


def _load(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path, obj):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, path)


def run(mirror_dir, appdata, anki_dir):
    res = {"ok": False, "at": time.time(), "required": None, "action": None, "error": None, "message": ""}
    hkey = None
    try:
        os.makedirs(mirror_dir, exist_ok=True)
        hkey, endpoint = read_auth(appdata)
        if not hkey:
            res["error"] = "이 PC 의 Anki 에 AnkiWeb 로그인이 없습니다 — Anki 를 열어 동기화(로그인)를 한 번 해 주세요"
            return res
        state_path = os.path.join(mirror_dir, "state.json")
        state = _load(state_path)
        endpoint = state.get("endpoint") or endpoint                 # 지난번에 서버가 알려 준 주소가 먼저
        os.add_dll_directory(anki_dir)
        sys.path.insert(0, os.path.join(anki_dir, "app_packages"))
        from anki.collection import Collection
        from anki import sync_pb2

        def auth_of(ep):
            a = sync_pb2.SyncAuth(hkey=hkey, io_timeout_secs=IO_TIMEOUT_SECS)
            if ep:
                a.endpoint = ep
            return a

        col = Collection(os.path.join(mirror_dir, "collection.anki2"))
        try:
            out = col.sync_collection(auth_of(endpoint), False)      # 미디어는 받지 않는다
            req = sync_pb2.SyncCollectionResponse.ChangesRequired.Name(out.required)
            res["required"] = req
            res["message"] = out.server_message or ""
            if out.new_endpoint:
                endpoint = out.new_endpoint
                state["endpoint"] = endpoint
                _save(state_path, state)
            act = decide(req)
            res["action"] = act
            if act == "download":                                    # 통째로 받기 — 올리기는 없다
                col.close_for_full_sync()
                col.full_upload_or_download(auth=auth_of(endpoint), server_usn=None, upload=False)
                col.reopen(after_full_sync=True)
            elif act == "stop":
                res["error"] = "AnkiWeb 이 '%s' 를 요구합니다 — 사본은 올리지 않으므로 멈춥니다" % req
                return res
            res["ok"] = True
        finally:
            col.close()
    except Exception as e:
        txt = "%s: %s" % (type(e).__name__, e)
        res["error"] = (txt.replace(hkey, "***") if hkey else txt)[:500]
        res["trace"] = (traceback.format_exc().replace(hkey, "***") if hkey else traceback.format_exc())[-1500:]
    return res


def main(argv):
    mirror_dir, appdata, anki_dir = argv[1], argv[2], argv[3]
    res = run(mirror_dir, appdata, anki_dir)
    try:
        _save(os.path.join(mirror_dir, "result.json"), res)
    except Exception:
        pass
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
