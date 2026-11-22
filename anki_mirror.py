# -*- coding: utf-8 -*-
"""
anki_mirror.py — Orbit 이 직접 AnkiWeb 에서 받아 오는 Anki 사본 (DESIGN §59)

§31 의 길(폰 → AnkiWeb → **이 PC 의 Anki 가 동기화** → 그 파일을 읽기)은 Anki 창을 켜고 눌러야 숫자가 왔다.
여기서는 Orbit 이 **자기 폴더의 사본**(`data\\anki_mirror\\collection.anki2`)을 따로 두고, 이 PC 에 깔린 Anki 의
동기화 엔진으로 그 사본을 AnkiWeb 과 맞춘다. 사용자의 Anki 프로필 모음은 건드리지 않는다 — Anki 가 켜져 있어도 되고,
덮어쓸지 고를 일도 없다(사본에는 지킬 변경이 없어 통째로 받기가 늘 안전하다. 올리지는 않는다).

Anki 꾸러미는 Anki 에 든 파이썬(3.13)의 .pyc 라 Orbit 의 파이썬(3.8)으로는 못 읽는다. 그래서 PowerShell 이
Anki 폴더의 `python3xx.dll` 의 `Py_Main` 을 불러 `anki_mirror_worker.py` 를 돌린다(창 없음).

켜고 끄기: 사본 폴더에 `on` 파일이 있으면 켜진 것(`enable()` 이 만든다). 폴더를 지우면 꺼진다.
표준 라이브러리만. `runner` 를 주입해 테스트한다.
"""
import base64
import glob
import io
import json
import logging
import os
import subprocess
import threading
import time

import common

SYNC_EVERY_SEC = 30 * 60        # 이 간격으로 받아 온다(누가 Orbit 을 보고 있을 때 — /api/anki 가 부른다)
RUN_TIMEOUT = 300               # 처음 한 번은 통째로 받는다
MIRROR_DIR = None               # 테스트가 바꾼다. 기본 data\anki_mirror
log = logging.getLogger("MAENG_worklog.server.anki_mirror")
_lock = threading.Lock()
_state = {"busy": False, "last_try": 0, "last_ok": 0, "error": None, "required": None, "action": None}


def mirror_dir(cfg=None):
    return MIRROR_DIR or os.path.join(common.data_dir(cfg), "anki_mirror")


def collection_path(cfg=None):
    return os.path.join(mirror_dir(cfg), "collection.anki2")


def find_anki(cfg=None):
    """이 PC 에 깔린 Anki 폴더와 그 파이썬 dll — (폴더, dll) 또는 (None, None)."""
    cands = [(cfg or {}).get("anki_dir"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Anki")]
    for d in cands:
        if not d or not os.path.isdir(os.path.join(d, "app_packages", "anki")):
            continue
        dlls = [p for p in glob.glob(os.path.join(d, "python3*.dll")) if os.path.basename(p).lower() != "python3.dll"]
        if dlls:
            return d, sorted(dlls)[-1]
    return None, None


def enabled(cfg=None):
    return os.path.exists(os.path.join(mirror_dir(cfg), "on"))


def enable(cfg=None):
    os.makedirs(mirror_dir(cfg), exist_ok=True)
    with io.open(os.path.join(mirror_dir(cfg), "on"), "w", encoding="utf-8") as f:
        f.write(u"Orbit 이 AnkiWeb 에서 직접 받아 오는 사본(DESIGN §59). 이 폴더를 지우면 꺼진다.\n")


def status():
    with _lock:
        return dict(_state)


def ps_command(dll, worker, args):
    """PowerShell 한 줄(-EncodedCommand 용 글): python3xx.dll 의 Py_Main 으로 worker 를 돌리고 그 결과 값으로 끝낸다."""
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    src = ('using System; using System.Runtime.InteropServices; public static class OrbitAnkiPy { '
           '[DllImport(@"%s", CharSet = CharSet.Unicode, CallingConvention = CallingConvention.Cdecl)] '
           'public static extern int Py_Main(int argc, [MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.LPWStr)] string[] argv); }'
           % dll.replace('"', '""'))
    argv = ["python", worker] + list(args)
    return ("$ErrorActionPreference = 'Stop'; Add-Type -TypeDefinition %s; exit [OrbitAnkiPy]::Py_Main(%d, @(%s))"
            % (q(src), len(argv), ", ".join(q(a) for a in argv)))


def _run_powershell(cmd, timeout):
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, timeout=timeout, **kw)
    return r.returncode, (r.stderr or b"").decode("utf-8", "replace")[-400:]


def sync_once(now=None, cfg=None, runner=None):
    """한 번 받아 온다(막힘). 결과를 _state 에 남기고 True/False."""
    now = now or time.time()
    d = mirror_dir(cfg)
    res = {"ok": False, "error": None}
    try:
        anki, dll = find_anki(cfg)
        if not anki:
            raise RuntimeError("이 PC 에 Anki 가 없습니다")
        os.makedirs(d, exist_ok=True)
        rp = os.path.join(d, "result.json")
        if os.path.exists(rp):
            os.remove(rp)
        worker = os.path.join(common.ROOT, "anki_mirror_worker.py")
        code, err = (runner or _run_powershell)(ps_command(dll, worker, [d, os.environ.get("APPDATA", ""), anki]), RUN_TIMEOUT)
        if os.path.exists(rp):
            with io.open(rp, encoding="utf-8") as f:
                res = json.load(f)
        else:
            res["error"] = "동기화가 결과 없이 끝났습니다(%s) %s" % (code, err.strip())
    except subprocess.TimeoutExpired:
        res["error"] = "AnkiWeb 에서 받다가 시간이 넘었습니다"
    except Exception as e:                                   # noqa: BLE001 — 받아 오기가 안 돼도 서버는 그대로
        res["error"] = "%s: %s" % (type(e).__name__, e)
    ok = bool(res.get("ok"))
    with _lock:
        _state.update({"busy": False, "error": None if ok else (res.get("error") or "알 수 없는 오류"),
                       "required": res.get("required"), "action": res.get("action")})
        if ok:
            _state["last_ok"] = now
    if not ok:
        log.warning("Anki 사본 받아 오기 실패: %s", res.get("error"))
    return ok


def maybe_sync(now=None, cfg=None, every=None, force=False, wait=False, runner=None):
    """켜져 있으면 `every` 초에 한 번 받아 온다. 시작했으면 True. 기본은 뒤에서(`wait=False`)."""
    now = now or time.time()
    every = SYNC_EVERY_SEC if every is None else every
    if not enabled(cfg):
        return False
    with _lock:
        if _state["busy"]:
            return False
        if not force and _state["last_try"] and now - _state["last_try"] < every:
            return False
        _state.update({"busy": True, "last_try": now})
    if wait:
        return sync_once(now, cfg, runner)
    threading.Thread(target=sync_once, args=(now, cfg, runner), name="anki-mirror", daemon=True).start()
    return True
