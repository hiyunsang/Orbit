# -*- coding: utf-8 -*-
"""
작업 표시줄 이름표 (DESIGN §46)

PC 에서는 Orbit·Athenaeum 이 크롬·엣지의 --app 창으로 뜬다. 그 창은 작업 표시줄에 브라우저가 만든 ID
('Chrome.127.0.0.1_/…', 'MSEdge.localhost_/')만 알리고 '다시 열 명령'과 아이콘은 알리지 않는다. 그래서 창을
작업 표시줄에 고정하면 윈도우는 브라우저 바로가기·브라우저 아이콘으로 고정한다.
여기서는 그런 창에 앱 자신의 ID(AppUserModelID)·다시 실행 명령·이름·아이콘을 붙인다. 그러면
  - 창을 고정하면 앱 실행기와 앱 아이콘으로 고정되고,
  - 같은 ID 를 단 바로가기(시작 메뉴·바탕화면)와 창이 작업 표시줄에서 한 단추로 묶인다.

- 무엇을 붙일지: orbit_app()(이 앱) + data\\taskbar_apps.json(이 PC 의 다른 앱 — 예: Athenaeum, 사용자별이라 git 밖).
- match()·load_apps()·wanted() 는 순수 함수(테스트). stamp_once()·stamp_shortcut() 만 win32 를 쓴다.
- 서버가 2초마다 붙이고(start), launch.pyw 는 창을 연 직후 바로 붙인다(고정한 단추로 곧장 묶이게).

명령줄(개발·한 번 손볼 때):
  python taskbar_id.py --once                          지금 떠 있는 창에 붙이고 무엇에 붙였는지 출력
  python taskbar_id.py --shortcut MAENG.Orbit a.lnk …  바로가기에 같은 ID 를 단다
"""
import io
import json
import logging
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BROWSERS = ("chrome.exe", "msedge.exe")
KEYS = ("name", "aumid", "title", "host", "command", "icon")
log = logging.getLogger("MAENG_worklog.server.taskbar")       # 서버 로그(server.log)로 간다


def pythonw_path():
    p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return p if os.path.isfile(p) else sys.executable


def orbit_app(root=ROOT, pythonw=None):
    """이 앱(Orbit). 제목은 '(3) Orbit · 출근기록' 처럼 앞에 안 읽은 수가 붙을 수 있다."""
    return {"name": "Orbit", "aumid": "MAENG.Orbit", "title": u"Orbit · 출근기록$", "host": "127.0.0.1",
            "command": u'"%s" "%s"' % (pythonw or pythonw_path(), os.path.join(root, "launch.pyw")),
            "icon": os.path.join(root, "logo", "orbit-mono.ico") + ",0"}


def load_apps(data_dir, root=ROOT, pythonw=None):
    """[Orbit] + data_dir\\taskbar_apps.json 의 올바른 항목(여섯 열쇠가 모두 비지 않은 글자). 파일이 없거나 깨지면 Orbit 만."""
    apps = [orbit_app(root, pythonw)]
    try:
        with io.open(os.path.join(data_dir, "taskbar_apps.json"), encoding="utf-8") as f:
            extra = json.load(f)
    except (OSError, ValueError):
        extra = []
    for a in extra if isinstance(extra, list) else []:
        if isinstance(a, dict) and all(isinstance(a.get(k), str) and a.get(k) for k in KEYS):
            try:
                re.compile(a["title"])
            except re.error:
                continue
            apps.append({k: a[k] for k in KEYS})
    return apps


def is_browser_app_id(cur, host):
    """브라우저가 --app 창에 다는 ID 인가 — 'Chrome.<host>_/…' · 'MSEdge.<host>_/…'. 보통 브라우저 창('Chrome')은 아니다."""
    return bool(re.match(r"^(Chrome|MSEdge)\.%s_" % re.escape(host), cur or ""))


def match(apps, title, exe, cur_aumid):
    """창 하나 → 붙일 앱(없으면 None). 브라우저의 --app 창(또는 이미 붙인 창)이고 제목이 그 앱의 것일 때만.
    같은 localhost 를 쓰는 다른 앱 창(Janua 등)은 제목이 달라 건드리지 않는다."""
    if os.path.basename(exe or "").lower() not in BROWSERS:
        return None
    for a in apps:
        if not re.search(a["title"], title or ""):
            continue
        if cur_aumid == a["aumid"] or is_browser_app_id(cur_aumid, a["host"]):
            return a
    return None


def wanted(app):
    """창에 붙일 값 — (속성 이름, 값). 크롬(ui/base/win/shell.cc)과 같은 차례: ID 먼저, 다시 실행 셋."""
    return [("ID", app["aumid"]), ("RelaunchIconResource", app["icon"]),
            ("RelaunchCommand", app["command"]), ("RelaunchDisplayNameResource", app["name"])]


# ------------------------------------------------------------------ win32 (테스트하지 않는 쪽)
def _keys():
    from win32com.propsys import pscon
    return {"ID": pscon.PKEY_AppUserModel_ID,
            "RelaunchIconResource": pscon.PKEY_AppUserModel_RelaunchIconResource,
            "RelaunchCommand": pscon.PKEY_AppUserModel_RelaunchCommand,
            "RelaunchDisplayNameResource": pscon.PKEY_AppUserModel_RelaunchDisplayNameResource}


def _exe_of(hwnd):
    import win32api
    import win32process
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    h = win32api.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    try:
        return win32process.GetModuleFileNameEx(h, 0)
    finally:
        win32api.CloseHandle(h)


def stamp_once(apps):
    """떠 있는 창 중 맞는 것에 붙인다. 이미 같은 값이면 건드리지 않는다. 붙인 [(hwnd, 앱 이름)] 를 돌려준다.
    부르는 스레드에서 pythoncom.CoInitialize() 를 먼저 해 둘 것."""
    import pythoncom
    import win32gui
    from win32com.propsys import propsys
    keys = _keys()
    hwnds = []
    win32gui.EnumWindows(lambda h, _: hwnds.append(h) or True, None)
    pats = [re.compile(a["title"]) for a in apps]
    done = []
    for h in hwnds:
        try:
            if not win32gui.IsWindowVisible(h):
                continue
            title = win32gui.GetWindowText(h)
            if not any(p.search(title) for p in pats):     # 거의 모든 창은 여기서 끝 — 프로세스는 열지 않는다
                continue
            st = propsys.SHGetPropertyStoreForWindow(h, propsys.IID_IPropertyStore)
            app = match(apps, title, _exe_of(h), st.GetValue(keys["ID"]).GetValue())
            if app is None:
                continue
            want = wanted(app)
            if all(st.GetValue(keys[k]).GetValue() == v for k, v in want):
                continue
            for k, v in want:
                st.SetValue(keys[k], propsys.PROPVARIANTType(v, pythoncom.VT_LPWSTR))
                st.Commit()
            done.append((h, app["name"]))
        except Exception as e:                           # 창이 그 사이 닫히는 등 — 다음 바퀴에 다시
            log.debug("작업 표시줄 이름표: 창 %s 건너뜀 (%s)", h, e)
    return done


def stamp_shortcut(path, aumid):
    """바로가기(.lnk)에 AppUserModelID 를 단다 — 창과 같은 ID 라야 작업 표시줄에서 한 단추로 묶인다."""
    import pythoncom
    from win32com.shell import shell
    from win32com.propsys import propsys, pscon
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    pf = link.QueryInterface(pythoncom.IID_IPersistFile)
    pf.Load(path, 2)                                     # STGM_READWRITE
    st = link.QueryInterface(propsys.IID_IPropertyStore)
    st.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(aumid, pythoncom.VT_LPWSTR))
    st.Commit()
    pf.Save(path, True)


def start(data_dir, every=2.0, root=ROOT):
    """서버 안의 데몬 스레드: every 초마다 stamp_once. taskbar_apps.json 은 바뀌면 다시 읽는다. 윈도우가 아니면 아무것도 안 한다."""
    if os.name != "nt":
        return None

    def loop():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception as e:
            log.warning("작업 표시줄 이름표 꺼짐 (pywin32 없음?): %s", e)
            return
        path = os.path.join(data_dir, "taskbar_apps.json")
        seen, apps, last_err = None, None, None
        while True:
            try:
                try:
                    mt = os.path.getmtime(path)
                except OSError:
                    mt = None
                if apps is None or mt != seen:
                    apps, seen = load_apps(data_dir, root), mt
                    log.info("작업 표시줄 이름표: %s", ", ".join(a["name"] for a in apps))
                for h, name in stamp_once(apps):
                    log.info("작업 표시줄 이름표: %s 창(%s)에 붙임", name, h)
                last_err = None
            except Exception as e:
                if str(e) != last_err:
                    log.warning("작업 표시줄 이름표 실패: %s", e)
                    last_err = str(e)
            time.sleep(every)

    t = threading.Thread(target=loop, name="taskbar-id", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    import pythoncom
    pythoncom.CoInitialize()
    sys.path.insert(0, ROOT)
    import common
    ddir = common.data_dir(common.load_config())
    if sys.argv[1:2] == ["--once"]:
        for h, name in stamp_once(load_apps(ddir)):
            print("붙임", name, h)
    elif sys.argv[1:2] == ["--shortcut"] and len(sys.argv) >= 4:
        for p in sys.argv[3:]:
            stamp_shortcut(p, sys.argv[2])
            print("바로가기", sys.argv[2], p)
    else:
        print(__doc__)
