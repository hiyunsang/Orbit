# -*- coding: utf-8 -*-
"""
Horarium 로고 생성기 — 참고 이미지와 같은 '슬라이스 세리프 H' (2026-09-06 사용자 최종: "이거랑 똑같이")

  두꺼운 두 기둥 + 얇은 평 세리프(살짝 브래킷) + 헤어라인 가로대가 가운데서 45° 로 갈라지며 오른쪽 조각이 조금 내려앉음
  + 왼 기둥 안쪽(가로대 위)·오른 기둥 안쪽(가로대 아래)에 대각선 홈. 가로대는 기둥 밖으로 세리프만큼 나감.
  세로보다 약간 넓은 비율. 색은 Athenaeum 과 같은 앤티크 골드.

python make_logo.py  →  이 폴더에
  mark.svg / mark-ink.svg     투명 배경 마크 (헤더용)
  lockup.svg / lockup.png     마크 + 가는 줄 + 자간 넓힌 HORARIUM
  favicon.svg                 배경 없는 골드 H, 정사각 캔버스 정중앙
  icon-16/24/32/48/180/192/256/512.png, horarium-mono.ico   (16·24px 은 가로대를 끊지 않은 굵은 H)
..\\static\\ 에 favicon.svg, logo-mark.svg, icon-192.png, icon-180.png, horarium.ico 복사, ..\\app_icon.ico 교체
글꼴 없이 전부 경로(path).
"""
import io, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "static")
GOLD, INK = "#C9A227", "#1c1c1a"
TOP, BASE = 70, 330                          # 글자 높이 260
LX0, LX1, RX0, RX1 = 72, 130, 270, 328       # 두 기둥 (두께 58), 가운데 200 대칭
SPREAD, SERIF_H, BRACKET = 28, 8, 10         # 세리프 폭·두께·브래킷 크기
BAR_H, STEP, GAP = 8, 12, 5                  # 가로대 두께, 좌우 단차, 대각선 틈 반폭
BAR_Y = 192                                  # 왼쪽 가로대 윗선 (오른쪽은 STEP 만큼 아래)
NOTCH_H, NOTCH_W = 34, 28                    # 기둥 안쪽 대각선 홈 (높이·깊이)


def poly(*pts):
    return "M" + " L".join("%s,%s" % (round(x, 1), round(y, 1)) for x, y in pts) + " Z"


def rect(x0, y0, x1, y1):
    return poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def serif(x0, x1, y, up):
    """얇은 평 세리프 + 기둥과 만나는 곳의 작은 브래킷(곡선 대신 둥근 사분면 근사)."""
    if up:
        plate = rect(x0 - SPREAD, y - SERIF_H, x1 + SPREAD, y)
        brL = "M%s,%s Q%s,%s %s,%s L%s,%s Z" % (x0 - BRACKET, y, x0, y, x0, y + BRACKET, x0, y)
        brR = "M%s,%s Q%s,%s %s,%s L%s,%s Z" % (x1 + BRACKET, y, x1, y, x1, y + BRACKET, x1, y)
    else:
        plate = rect(x0 - SPREAD, y, x1 + SPREAD, y + SERIF_H)
        brL = "M%s,%s Q%s,%s %s,%s L%s,%s Z" % (x0 - BRACKET, y, x0, y, x0, y - BRACKET, x0, y)
        brR = "M%s,%s Q%s,%s %s,%s L%s,%s Z" % (x1 + BRACKET, y, x1, y, x1, y - BRACKET, x1, y)
    return [plate, brL, brR]


def mark_d():
    yL0, yL1 = BAR_Y, BAR_Y + BAR_H
    yR0, yR1 = yL0 + STEP, yL1 + STEP
    yc = (yL0 + yR1) / 2.0
    left_edge = lambda y: 200 + (yc - y) - GAP       # 왼 조각 오른쪽 끝선 (45°)
    right_edge = lambda y: 200 + (yc - y) + GAP      # 오른 조각 왼쪽 시작선
    left_stem = poly((LX0, TOP), (LX1, TOP), (LX1, yL0 - NOTCH_H), (LX1 - NOTCH_W, yL0), (LX1, yL0), (LX1, BASE), (LX0, BASE))
    right_stem = poly((RX0, TOP), (RX1, TOP), (RX1, BASE), (RX0, BASE), (RX0, yR1 + NOTCH_H), (RX0 + NOTCH_W, yR1), (RX0, yR1))
    bar_left = poly((LX0 - SPREAD, yL0), (left_edge(yL0), yL0), (left_edge(yL1), yL1), (LX0 - SPREAD, yL1))
    bar_right = poly((right_edge(yR0), yR0), (RX1 + SPREAD, yR0), (RX1 + SPREAD, yR1), (right_edge(yR1), yR1))
    parts = [left_stem, right_stem, bar_left, bar_right]
    parts += serif(LX0, LX1, TOP, True) + serif(RX0, RX1, TOP, True) + serif(LX0, LX1, BASE, False) + serif(RX0, RX1, BASE, False)
    return " ".join(parts)


def bold_H_d():
    """16px 용 단순판: 굵은 H, 가로대는 끊지 않음."""
    return " ".join([rect(76, TOP, 140, BASE), rect(260, TOP, 324, BASE), rect(140, 186, 260, 216),
                     rect(60, TOP - 14, 156, TOP), rect(244, TOP - 14, 340, TOP), rect(60, BASE, 156, BASE + 14), rect(244, BASE, 340, BASE + 14)])


X0, X1 = LX0 - SPREAD, RX1 + SPREAD            # 44 .. 356
Y0, Y1 = TOP - SERIF_H, BASE + SERIF_H         # 62 .. 338
MARK_VIEWBOX = "%s %s %s %s" % (X0 - 6, Y0 - 6, (X1 - X0) + 12, (Y1 - Y0) + 12)


def mark_svg(color):
    return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='%s'><path fill='%s' d='%s'/></svg>" % (MARK_VIEWBOX, color, mark_d())


def alone_svg(color, fill_ratio=0.82, simple=False):
    d = bold_H_d() if simple else mark_d()
    x0, x1 = (60, 340) if simple else (X0, X1)
    y0, y1 = (TOP - 14, BASE + 14) if simple else (Y0, Y1)
    w, h = x1 - x0, y1 - y0
    s = 400.0 * fill_ratio / max(w, h)
    tx, ty = 200 - (x0 + w / 2.0) * s, 200 - (y0 + h / 2.0) * s
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'><g transform='translate(%.2f,%.2f) scale(%.4f)'><path fill='%s' d='%s'/></g></svg>"
            % (tx, ty, s, color, d))


def lockup_svg(color, text_color):
    s = 250.0 / (Y1 - Y0)
    tx, ty = 450 - (X0 + (X1 - X0) / 2.0) * s, 20 - Y0 * s
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 900 380'>"
            "<g transform='translate(%.2f,%.2f) scale(%.4f)'><path fill='%s' d='%s'/></g>"
            "<line x1='120' y1='300' x2='780' y2='300' stroke='%s' stroke-width='2'/>"
            "<text x='450' y='356' text-anchor='middle' font-family='Georgia, \"Times New Roman\", serif' font-size='50' letter-spacing='14' fill='%s'>HORARIUM</text>"
            "</svg>") % (tx, ty, s, color, mark_d(), color, text_color)


def write(name, text):
    io.open(os.path.join(HERE, name), "w", encoding="utf-8").write(text)


def render_png(svg_text, size, name):
    import fitz
    doc = fitz.open("svg", svg_text.encode("utf-8"))
    page = doc[0]
    z = size / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=True)
    pix.save(os.path.join(HERE, name))


def main():
    write("mark.svg", mark_svg(GOLD))
    write("mark-ink.svg", mark_svg(INK))
    write("lockup.svg", lockup_svg(GOLD, INK))
    write("favicon.svg", alone_svg(GOLD))
    for n in (32, 48, 180, 192, 256, 512):
        render_png(alone_svg(GOLD), n, "icon-%d.png" % n)
    render_png(alone_svg(GOLD, 0.92, simple=True), 16, "icon-16.png")
    render_png(alone_svg(GOLD, 0.92, simple=True), 24, "icon-24.png")
    render_png(lockup_svg(GOLD, INK), 900, "lockup.png")
    render_png(mark_svg(GOLD), 68, "mark-34px-preview@2x.png")
    from PIL import Image
    imgs = {n: Image.open(os.path.join(HERE, "icon-%d.png" % n)).convert("RGBA") for n in (16, 24, 32, 48, 256)}
    ico = os.path.join(HERE, "horarium-mono.ico")
    imgs[256].save(ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)],
                   append_images=[imgs[16], imgs[24], imgs[32], imgs[48]])
    for src, dst in [("favicon.svg", "favicon.svg"), ("mark.svg", "logo-mark.svg"), ("icon-192.png", "icon-192.png"),
                     ("icon-180.png", "icon-180.png"), ("horarium-mono.ico", "horarium.ico")]:
        shutil.copyfile(os.path.join(HERE, src), os.path.join(STATIC, dst))
    shutil.copyfile(ico, os.path.join(ROOT, "app_icon.ico"))
    for stale in ("horarium-calli.ico", "horarium-calli-v2.ico", "horarium-timeline.ico", "horarium-axis.ico", "horarium-sliced.ico", "horarium-script.ico"):
        p = os.path.join(HERE, stale)
        if os.path.exists(p):
            os.remove(p)
    print("logo files written to", HERE)


if __name__ == "__main__":
    main()
