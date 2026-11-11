# -*- coding: utf-8 -*-
"""
Orbit(옛 Horarium, 출근기록) 실행기 (pythonw 전용, 콘솔 창 없음)

- 수집기(tracker.py)와 서버(server.py)를 숨김으로 띄운다. 이미 켜져 있으면 각자 알아서 종료된다.
- 서버가 응답할 때까지 최대 8초 기다린 뒤, 앱 전용 브라우저 프로필로 화면을 앱 창처럼 연다.
- 인자 --no-browser : 창은 열지 않고 수집기/서버만 띄운다 (시작프로그램용).
- 문제가 생기면 메시지 상자로 알려 주고 data\\launch.log 에 한 줄 남긴다.

바탕화면 바로가기 → pythonw.exe "이 파일"  /  출근기록_열기.bat 도 이 파일을 호출한다.
"""
import os
import sys
import time
import json
import ctypes
import subprocess
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def _stamp_soon(ddir):
    """§46 창이 뜨자마자 작업 표시줄 이름표를 붙인다 — 서버도 2초마다 붙이지만, 고정한 단추로 곧장 묶이게. 최대 10초."""
    try:
        import pythoncom
        import taskbar_id
        pythoncom.CoInitialize()
        apps = taskbar_id.load_apps(ddir)
        end = time.time() + 10
        while time.time() < end:
            if taskbar_id.stamp_once(apps):
                return
            time.sleep(0.25)
    except Exception as e:
        log("작업 표시줄 이름표 실패: %s" % e)


def msgbox(text, title="Orbit", icon=0x10):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, icon)
    except Exception:
        pass


def pythonw_path():
    exe = sys.executable or ""
    d = os.path.dirname(exe)
    cand = os.path.join(d, "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def main():
    no_browser = "--no-browser" in sys.argv[1:]
    try:
        import common
        cfg = common.load_config()
        ddir = common.data_dir(cfg)
    except Exception as e:
        msgbox("설정을 읽지 못했습니다:\n%s" % e)
        return 1
    url = "http://%s:%s" % (cfg.get("host", "127.0.0.1"), cfg.get("port", 8771))
    logp = os.path.join(ddir, "launch.log")

    def log(msg):
        try:
            with open(logp, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

    pyw = pythonw_path()

    # 코드 업데이트 후 수집기를 사용자 문맥에서 새로 띄우기 위한 1회성 스위치:
    # data\restart_tracker.flag 가 있으면 실행 중인 수집기를 끝내고(아래에서 새로 띄움) 플래그를 지운다.
    flag = os.path.join(ddir, "restart_tracker.flag")
    if os.path.exists(flag):
        try:
            cmd = ('Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "pythonw.exe" -and '
                   '$_.CommandLine -like "*MAENG_worklog*tracker.py*" } | ForEach-Object { $_.ProcessId }')
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                 timeout=40, creationflags=0x08000000).stdout.decode("utf-8", "replace")
            pids = [p for p in out.split() if p.strip().isdigit()]
            for p in pids:
                subprocess.run(["taskkill", "/F", "/PID", p], capture_output=True, creationflags=0x08000000)
            log("restart_tracker.flag: 수집기 %s 종료 후 재시작" % (pids or "없음"))
            time.sleep(1.5)
        except Exception as e:
            log("restart_tracker.flag 처리 실패: %s" % e)
        try:
            os.remove(flag)
        except Exception:
            pass

    for script in ("tracker.py", "server.py"):
        try:
            subprocess.Popen([pyw, os.path.join(ROOT, script)], cwd=ROOT, creationflags=DETACHED,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
        except Exception as e:
            log("%s 실행 실패: %s" % (script, e))
            msgbox("%s 를 실행하지 못했습니다:\n%s" % (script, e))
            return 1

    # 서버 응답 대기 (최대 8초)
    ok = False
    for _ in range(16):
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=1) as r:
                json.loads(r.read().decode("utf-8"))
            ok = True
            break
        except Exception:
            time.sleep(0.5)
    log("서버 %s (%s), browser=%s" % ("응답" if ok else "무응답", url, not no_browser))
    if not ok:
        msgbox("서버가 아직 응답하지 않습니다.\n잠시 후 다시 열어 보세요.\n계속 안 되면 data\\server.log 를 확인하세요.", icon=0x30)
    if no_browser:
        return 0

    profile = os.path.join(ddir, "browser-profile")
    browser = CHROME if os.path.exists(CHROME) else (EDGE if os.path.exists(EDGE) else None)
    try:
        if browser:
            subprocess.Popen([browser, "--user-data-dir=" + profile, "--no-first-run",
                              "--no-default-browser-check", "--app=" + url],
                             creationflags=0x00000008 | 0x00000200, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _stamp_soon(ddir)
        else:
            os.startfile(url)
    except Exception as e:
        log("브라우저 실행 실패: %s" % e)
        msgbox("브라우저를 열지 못했습니다. 주소창에 %s 를 직접 입력하세요.\n%s" % (url, e))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # pythonw 라 콘솔이 없으므로 반드시 알림
        msgbox("실행 중 오류:\n%s" % e)
        sys.exit(1)
