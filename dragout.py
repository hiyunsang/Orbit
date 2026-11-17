# -*- coding: utf-8 -*-
"""
서랍 파일을 진짜 파일로 끌어 내기 (DESIGN §54) — Windows 전용.

크롬은 웹 페이지가 끄는 파일을 '내려받기 링크'(DownloadURL)로만 넘긴다. 그 링크는 탐색기 · 바탕화면처럼
비동기로 받는 곳에서만 파일이 되고, 다른 크롬 창의 업로드 칸 · 카톡 · 한글에는 아무것도 넘어가지 않는다
(크롬 os_exchange_data_provider_win.cc: 끌기 도중의 GetData 는 실패, 비동기로 시작한 쪽만 기다려 준다).
그래서 이 PC 에서는 화면이 '끌기 시작' 을 알려 주면 서버가 윈도 끌기(OLE DoDragDrop)를 대신 시작한다.
넘기는 것은 탐색기가 넘기는 것과 같은 셸 파일 객체(CF_HDROP · 파일 내용 · 셸 ID 목록) — 어디에 놓아도 진짜 파일이다.

마우스 단추는 이미 크롬 창에서 눌려 있다. 윈도 끌기는 마우스 메시지로 도는데, 그 메시지는 크롬 스레드로 간다.
그래서 끌기 스레드를 그 창의 스레드 입력에 붙인다(AttachThreadInput) — 그래야 OLE 가 마우스를 잡는다.
붙인 동안 이 스레드가 멈추면 크롬 입력도 멈추므로:
  - 단추가 이미 떼어졌거나, 맨 앞 창이 Orbit 이 아니면 시작하지 않는다
  - 한 번에 하나만, 끌기는 복사만(옮기기 없음 — 서랍 사본이 사라지지 않게)
  - 감시: 단추를 뗀 뒤 0.5초가 지나도 안 끝나거나 60초가 지나면 Esc 를 넣어 취소
  - 끝나면 반드시 떼어 낸다(finally)

precheck() · export_copy() 는 win32 없이 돈다(테스트). start() 부터 win32.
"""
import logging
import os
import shutil
import threading
import time

TITLE_MARK = u"Orbit"          # 맨 앞 창 제목에 이것이 있어야 시작(앱 창 '(2) Orbit · 출근기록', 탭이면 '… - Chrome')
STUCK_AFTER_UP = 0.5           # 단추를 뗀 뒤 이만큼 지나도 안 끝나면 취소
MAX_SEC = 60.0                 # 이보다 오래 들고 있으면 취소
DROPEFFECT_COPY = 1

log = logging.getLogger("MAENG_worklog.server.dragout")
_lock = threading.Lock()
_busy = [False]


def precheck(title, button_down, busy):
    """끌기를 시작해도 되는가 → (True, "") 또는 (False, 이유). 순수 함수."""
    if busy:
        return False, "busy"
    if TITLE_MARK not in (title or u""):
        return False, "not-orbit"
    if not button_down:
        return False, "button-up"
    return True, ""


def export_copy(src, out_dir, name):
    """끌어 낼 사본 out_dir\\name 을 돌려준다. 서랍 안 파일 이름에는 'id_' 가 붙어 있어 원래 이름으로 한 벌 둔다.
    크기 · 고친 때가 같으면 다시 쓰지 않고, 이름이 바뀌었으면 옛 사본은 지운다."""
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, name)
    for f in os.listdir(out_dir):
        if f != name:
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    try:
        s, d = os.stat(src), os.stat(dst)
        if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
            return dst
    except OSError:
        pass
    shutil.copy2(src, dst)
    return dst


# ------------------------------------------------------------------ 여기부터 win32
def _button_down():
    import win32api
    import win32con
    vk = win32con.VK_RBUTTON if win32api.GetSystemMetrics(win32con.SM_SWAPBUTTON) else win32con.VK_LBUTTON
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)


def data_object(path):
    """탐색기가 넘기는 것과 같은 셸 파일 객체(IDataObject)."""
    import pythoncom
    from win32com.shell import shell
    d, name = os.path.split(os.path.abspath(path))
    desktop = shell.SHGetDesktopFolder()
    pidl = shell.SHParseDisplayName(d, 0)[0]
    folder = desktop.BindToObject(pidl, None, shell.IID_IShellFolder)
    child = folder.ParseDisplayName(0, None, name)[1]
    return folder.GetUIObjectOf(0, [child], pythoncom.IID_IDataObject, 0)[1]


try:
    import pythoncom as _pythoncom
    _IID_DROPSOURCE = _pythoncom.IID_IDropSource
except ImportError:                                  # win32 없는 곳(테스트의 순수 함수만)
    _IID_DROPSOURCE = None


class _DropSource(object):
    _com_interfaces_ = [_IID_DROPSOURCE]             # 없으면 wrap 이 E_NOINTERFACE 로 실패한다
    _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]

    def QueryContinueDrag(self, esc, keys):
        import winerror
        if esc:
            return winerror.DRAGDROP_S_CANCEL
        if not _button_down():                     # 실제 단추 상태로 본다(붙이기가 안 됐어도 놓는 때는 맞게)
            return winerror.DRAGDROP_S_DROP
        return 0

    def GiveFeedback(self, effect):
        import winerror
        return winerror.DRAGDROP_S_USEDEFAULTCURSORS


def _watch(tid, done):
    """멈춘 끌기를 풀어 준다 — 단추를 뗐는데도 안 끝나면, 또는 너무 오래면 끌기 스레드에 Esc."""
    import win32api
    import win32con
    t0 = time.time()
    up_at = None
    while not done.wait(0.05):
        now = time.time()
        if _button_down():
            up_at = None
        elif up_at is None:
            up_at = now
        if (up_at is not None and now - up_at > STUCK_AFTER_UP) or now - t0 > MAX_SEC:
            try:
                win32api.PostThreadMessage(tid, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
            except Exception:
                return
            up_at = now


def _run(path, hwnd, ready, box):
    import pythoncom
    import win32api
    import win32con
    import win32gui
    import win32process
    from win32com.server.util import wrap
    me = win32api.GetCurrentThreadId()
    tid = None
    attached = False
    done = threading.Event()
    com = False
    try:
        pythoncom.OleInitialize()
        com = True
        win32gui.PeekMessage(None, 0, 0, win32con.PM_NOREMOVE)      # 붙이기 전에 이 스레드의 메시지 큐를 만든다
        tid = win32process.GetWindowThreadProcessId(hwnd)[0]
        if tid and tid != me:
            win32process.AttachThreadInput(me, tid, True)
            attached = True
        if not _button_down():
            box["why"] = "button-up"
            return
        dobj = data_object(path)
        src = wrap(_DropSource(), pythoncom.IID_IDropSource)
        threading.Thread(target=_watch, args=(me, done), name="drawer-drag-watch", daemon=True).start()
        box["started"] = True
        ready.set()
        box["effect"] = pythoncom.DoDragDrop(dobj, src, DROPEFFECT_COPY)
    except Exception as e:                               # noqa: BLE001 — 끌기가 안 돼도 서버는 그대로
        box["why"] = "error: %s" % e
        log.warning("서랍 끌기 실패: %s", e)
    finally:
        done.set()
        if attached:
            try:
                win32process.AttachThreadInput(me, tid, False)
            except Exception:
                pass
        if com:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
        with _lock:
            _busy[0] = False
        ready.set()


def start(path, wait=0.6):
    """맨 앞 창(Orbit)에서 단추가 눌려 있으면 path 를 끌기 시작한다. {"started": bool, "why": 이유}."""
    import win32gui
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd) if hwnd else u""
    down = _button_down()
    with _lock:
        ok, why = precheck(title, down, _busy[0])
        if not ok:
            return {"started": False, "why": why}
        _busy[0] = True
    ready = threading.Event()
    box = {}
    try:
        threading.Thread(target=_run, args=(path, hwnd, ready, box), name="drawer-drag", daemon=True).start()
    except Exception:
        with _lock:
            _busy[0] = False
        raise
    ready.wait(wait)
    started = bool(box.get("started"))
    return {"started": started, "why": "" if started else box.get("why", "slow")}
