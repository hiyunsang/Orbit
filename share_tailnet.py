# -*- coding: utf-8 -*-
"""
share_tailnet.py — 집 노트북·휴대폰에서도 Horarium 을 열 수 있게 한다 (DESIGN §27, §36).

Tailscale(내 기기끼리만 통하는 사설망)로 이 PC 의 Horarium(8771)과 Athenaeum 단어장(8770)을 넘겨 준다.
서버는 계속 127.0.0.1 에만 열려 있고, tailscale 이 사설망 쪽에서 받아 넘겨 주는 방식이라 방화벽 설정이 필요 없다.
인터넷에 공개하는 것(Funnel)이 아니다.

§36: Horarium 은 테일넷 HTTPS(https://<이 PC>.<테일넷>.ts.net:8771)로 넘긴다 — 안 되면 http. 밖에서 열려면
Horarium 화면의 기록 → 다른 기기 에서 비밀번호를 정하고 켜야 한다(꺼져 있으면 밖의 요청은 모두 거절).
쓰는 법: Tailscale 을 설치하고 로그인한 뒤 'Horarium_집에서_열기_설정.bat' 을 더블클릭(화면의 「연결 설정」 과 같은 일).
되돌리기: tailscale serve reset
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PORTS = [(8771, "Orbit"), (8770, "Athenaeum (단어장)")]
CANDIDATES = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def find_exe():
    for p in CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("tailscale")


def run(exe, *args, timeout=40):
    try:
        r = subprocess.run([exe] + list(args), capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "(시간 초과)"
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


def local_ok(port):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=4).read(1)
        return True
    except Exception:
        return False


def main():
    print("=== Orbit(옛 Horarium) 을 집에서 열기 (Tailscale) ===")
    print()
    exe = find_exe()
    if not exe:
        print("[1] Tailscale 이 아직 설치돼 있지 않습니다.")
        print("    https://tailscale.com/download/windows 에서 받아 설치하고 로그인한 뒤 이 파일을 다시 실행하세요.")
        return 2
    print("[1] Tailscale 찾음:", exe)

    rc, out = run(exe, "status", "--json")
    try:
        st = json.loads(out)
    except Exception:
        st = None
    if rc != 0 or not st or st.get("BackendState") != "Running":
        print("[2] Tailscale 이 켜져 있지만 로그인(연결)이 안 됐습니다. 상태:", (st or {}).get("BackendState") or out[:200])
        print("    작업 표시줄의 Tailscale 아이콘을 눌러 로그인한 뒤 다시 실행하세요.")
        return 3
    me = st.get("Self") or {}
    ips = [ip for ip in (me.get("TailscaleIPs") or []) if "." in ip]
    dns = (me.get("DNSName") or "").rstrip(".")
    short = dns.split(".")[0] if dns else (me.get("HostName") or "")
    print("[2] 연결됨. 이 PC 의 사설망 이름:", dns or short, "/ 주소:", ", ".join(ips) or "?")
    print()

    failed = False
    for port, name in PORTS:
        if not local_ok(port):
            print("[3] %s 가 이 PC 에서 꺼져 있습니다 (127.0.0.1:%d). 켠 뒤 다시 실행하세요." % (name, port))
            failed = True
            continue
        how = "https" if port == 8771 else "http"      # §36.3 Horarium 은 HTTPS(보안 컨텍스트), Athenaeum 은 http 그대로
        rc, out = run(exe, "serve", "--bg", "--%s=%d" % (how, port), "http://127.0.0.1:%d" % port)
        if rc != 0 and how == "https":
            print("    (테일넷 HTTPS 가 안 돼 http 로 엽니다: %s)" % out[:160])
            rc, out = run(exe, "serve", "--bg", "--http=%d" % port, "http://127.0.0.1:%d" % port)
        if rc == 0:
            print("[3] %s 넘겨 주기 설정됨 (포트 %d)" % (name, port))
        else:
            failed = True
            print("[3] %s 설정 실패 (포트 %d):" % (name, port))
            print("    " + out.replace("\n", "\n    "))
    print()
    rc, out = run(exe, "serve", "status")
    print("현재 넘겨 주는 것:")
    print("    " + (out or "(없음)").replace("\n", "\n    "))
    print()

    lines = []
    host = short or (ips[0] if ips else "")
    if host:
        lines.append("집 노트북·휴대폰 브라우저에서 (그 기기에도 Tailscale 을 깔고 같은 계정으로 로그인):")
        rc2, st_out = run(exe, "serve", "status")
        hor = [t for t in (ln.strip().split()[0] for ln in st_out.splitlines() if ln.strip()) if t.startswith("http") and t.endswith(":8771")]
        lines.append("  Orbit      %s" % (hor[0] if hor else "http://%s:8771" % host))
        lines.append("  (처음 한 번: Orbit 화면 → 기록 → 다른 기기 에서 비밀번호를 정하고 켜기)")
        lines.append("  단어 맞추기  Orbit 의 '단어 맞추기 열기' 버튼 또는 http://%s:8770/vocab/quiz" % host)
    for ln in lines:
        print(ln)
    try:
        with open(os.path.join(HERE, "집에서_여는_주소.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n되돌리기: tailscale serve reset\n")
        print()
        print("이 주소를 '집에서_여는_주소.txt' 에 적어 두었습니다.")
    except Exception:
        pass
    if failed:
        print()
        print("일부가 실패했습니다. 위 메시지를 Claude 에게 보여 주세요.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
