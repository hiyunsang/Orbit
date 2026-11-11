# -*- coding: utf-8 -*-
"""
Orbit 로고 생성기 (DESIGN §37) — 옛 이름 Horarium 의 금색 H(make_logo.py)를 대신한다.

  마크: O 자리 — 가운데 원 + 둘레 고리 + 작은 위성. (§37.7: 색 없이 잉크 한 색, 고리는 글자 획의 48%)
        MOON > 0 이면 고리 위 오른쪽 위에 위성을 얹고 위성 둘레만큼 고리를 끊는다(§37.2 처음 판).
  글자: 맑은 고딕 Regular 의 라틴 글자(Segoe UI 계열), 대문자, 자간 0.14em — 사용자가 고른 글씨(§36 꺼짐 안내 화면).
        아이폰에는 이 글꼴이 없으므로 윤곽을 SVG 경로로 뽑는다.

실행(개발 도구, 앱 실행에는 필요 없음):  C:\\Users\\PC1\\anaconda3\\python.exe logo\\make_orbit.py
내놓는 것: ..\\static\\ logo-mark.svg · favicon.svg · icon-180/192/512.png · horarium.ico
          이 폴더  orbit.ico(바탕화면 바로가기) · orbit-preview.png · wordmark-o.svg(마크가 O 인 판, 보관)
"""
import io
import math
import os

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "static")
FONT = r"C:\Windows\Fonts\malgun.ttf"
TRACK = 0.14                      # 자간 (em)
INK, BLUE, WHITE = "#1c1c1a", "#1f4fd1", "#ffffff"
ICON_BG, ICON_INK = "#000000", "#f2f2ef"      # §39.1 아이콘·파비콘은 테마와 무관하게 검은 바탕 + 밝은 마크(우주)

# ---- 모양 (§37.6 미니멀, 2026-11-11 사용자: "그냥 색 없이 가운데 원에 더 얇은 고리로 … 미니멀하게") ----
RING = 0.48                       # 고리 굵기 / O 획 굵기 (처음 판 0.90, §37.6 A 0.55, §37.7 C 를 조금 더 얇게)
PLANET = 0.44                     # 가운데 원 반지름 / 고리 안반지름
PLANET_COLOR = INK                # 처음 판은 BLUE
MOON = 1.83                       # 위성 반지름 / 고리 굵기 (0 = 위성 없음). §37.7: C(0.55×1.6)와 같은 절대 크기
MOON_DEG = 40                     # 위성 자리 (오른쪽 위)
GAP = 0.92                        # 위성 둘레 틈 / 고리 굵기 (C 와 같은 절대 크기)
ICON_RING = {16: 0.072, 24: 0.056, 32: 0.048}   # 홀로 쓰는 아이콘의 고리 굵기 / 아이콘 크기 (작을수록 굵게 — 1px 아래로 안 가게)
ICON_RING_BIG = 0.040


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ------------------------------------------------------------------ 글꼴 재기
font = TTFont(FONT)
gs = font.getGlyphSet()
cmap = font.getBestCmap()
UPM = font["head"].unitsPerEm


def gname(ch):
    return cmap[ord(ch)]


def bounds_of(rec_value):
    bp = BoundsPen(gs)
    for op, args in rec_value:
        getattr(bp, op)(*args)
    return bp.bounds


def recorded(name):
    rp = RecordingPen()
    gs[name].draw(rp)
    return rp.value


def contours(name):
    out, cur = [], []
    for op, args in recorded(name):
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            out.append(cur)
            cur = []
    return out


o_cont = sorted((bounds_of(c) for c in contours(gname("O"))), key=lambda b: -(b[2] - b[0]))
(oxmin, oymin, oxmax, oymax), (ixmin, iymin, ixmax, iymax) = o_cont[0], o_cont[1]
O_STROKE = (((oxmax - oxmin) - (ixmax - ixmin)) + ((oymax - oymin) - (iymax - iymin))) / 4.0   # O 옆·위아래 굵기의 평균
RO = (oymax - oymin) / 2.0                                # 바깥 반지름 = O 높이의 반 (오버슈트 포함)
O_LSB = font["hmtx"][gname("O")][1]
O_RSB = font["hmtx"][gname("O")][0] - O_LSB - (oxmax - oxmin)
CY = (oymax + oymin) / 2.0


def stroke():
    return O_STROKE * RING


def orbit_geom(ro, s):
    """바깥 반지름 ro·굵기 s 의 마크 — 원점 = 고리 중심, y 위쪽."""
    r = ro - s / 2.0
    a = math.radians(MOON_DEG)
    m = MOON * s
    return {"R": r, "s": s, "p": PLANET * (ro - s), "mx": r * math.cos(a), "my": r * math.sin(a), "m": m, "g": m + GAP * s}


# ------------------------------------------------------------------ SVG
def _f(v):
    return ("%.1f" % v).rstrip("0").rstrip(".")


def svg_mark_elems(cx, cy_down, geo, mid):
    """y 아래로 가는 좌표계의 SVG 조각. mid = 위성이 있을 때의 mask id."""
    R, s, p, m, g = geo["R"], geo["s"], geo["p"], geo["m"], geo["g"]
    if m <= 0:
        return ('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="%s" stroke-width="%s"/>'
                '<circle cx="%s" cy="%s" r="%s" fill="%s"/>') % (
            _f(cx), _f(cy_down), _f(R), INK, _f(s), _f(cx), _f(cy_down), _f(p), PLANET_COLOR)
    mx, my = cx + geo["mx"], cy_down - geo["my"]
    big = R + s * 2
    return (
        '<mask id="%s" maskUnits="userSpaceOnUse" x="%s" y="%s" width="%s" height="%s">'
        '<rect x="%s" y="%s" width="%s" height="%s" fill="#fff"/><circle cx="%s" cy="%s" r="%s" fill="#000"/></mask>'
        '<circle cx="%s" cy="%s" r="%s" fill="none" stroke="%s" stroke-width="%s" mask="url(#%s)"/>'
        '<circle cx="%s" cy="%s" r="%s" fill="%s"/><circle cx="%s" cy="%s" r="%s" fill="%s"/>'
    ) % (mid, _f(cx - big), _f(cy_down - big), _f(2 * big), _f(2 * big),
         _f(cx - big), _f(cy_down - big), _f(2 * big), _f(2 * big), _f(mx), _f(my), _f(g),
         _f(cx), _f(cy_down), _f(R), INK, _f(s), mid,
         _f(cx), _f(cy_down), _f(p), PLANET_COLOR, _f(mx), _f(my), _f(m), INK)


def glyph_path(ch, dx, top):
    pen = SVGPathPen(gs)
    gs[gname(ch)].draw(pen)
    return '<path transform="translate(%.1f %.1f) scale(1 -1)" d="%s"/>' % (dx, top, pen.getCommands())


def build_wordmark():
    track = TRACK * UPM
    geo = orbit_geom(RO, stroke())
    top = oymax                                   # 위 끝 = O 꼭대기(오버슈트)
    height = oymax - oymin
    parts = [svg_mark_elems(RO, top - CY, geo, "om")]
    x = 2 * RO + O_RSB + track
    last_right = 0
    for ch in "RBIT":
        name = gname(ch)
        adv = font["hmtx"][name][0]
        parts.append(glyph_path(ch, x, top))
        last_right = x + bounds_of(recorded(name))[2]
        x += adv + track
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.0f %.0f" role="img" aria-label="ORBIT">'
           '<title>ORBIT</title><g fill="%s">%s</g></svg>\n') % (last_right, height, INK, "".join(parts))
    return svg, last_right / height


def build_mark_svg(pad=0.0, backing=False):
    """마크 하나 — viewBox 정사각. backing: 흰 원판(탭·어두운 바탕에서도 보이게)"""
    geo = orbit_geom(RO, stroke())
    half = RO + pad
    back = '<circle cx="%s" cy="%s" r="%s" fill="%s"/>' % (_f(half), _f(half), _f(half), WHITE) if backing else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" role="img" aria-label="Orbit">%s%s</svg>\n'
            % (_f(2 * half), _f(2 * half), back, svg_mark_elems(half, half, geo, "mm")))


def build_mark_svg_clock():
    """§37.9 분침 판 — 위성을 12시(0분)에 두고, 화면이 돌릴 두 원(고리를 끊는 틈·위성)에 class 를 붙인다."""
    global MOON_DEG
    keep = MOON_DEG
    MOON_DEG = 90
    try:
        svg = build_mark_svg().replace('id="mm"', 'id="omc"').replace("url(#mm)", "url(#omc)")
    finally:
        MOON_DEG = keep
    assert MOON > 0
    svg = svg.replace(INK, "currentColor")          # §39.1 화면에 끼워 넣는 판은 글자색(테마)을 따른다
    svg = svg.replace('fill="#000"/>', 'fill="#000" class="om-gap"/>')
    head, last = svg.rsplit("<circle ", 1)
    return head + '<circle class="om-moon" ' + last


# ------------------------------------------------------------------ PNG (Pillow, 8배로 그려 줄인다)
def icon_ring(size):
    for k in sorted(ICON_RING):
        if size <= k:
            return ICON_RING[k]
    return ICON_RING_BIG


def render_icon(size, bg="square", scale=0.70):
    """size px 아이콘. bg: 'square' 흰 정사각 / 'disc' 흰 원판 / None 투명. scale = 고리 바깥 지름 / size"""
    ss = 8
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2.0
    if bg == "square":
        d.rectangle([0, 0, S, S], fill=rgb(ICON_BG) + (255,))
    elif bg == "disc":
        d.ellipse([0, 0, S - 1, S - 1], fill=rgb(ICON_BG) + (255,))
    ro = S * scale / 2.0
    s = max(S * icon_ring(size) * (scale / 0.70), ss * 1.0)       # 고리는 1px 아래로 가지 않게
    geo = orbit_geom(ro, s)
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse([c - ro, c - ro, c + ro, c + ro], fill=rgb(ICON_INK) + (255,))
    ri = ro - s
    rd.ellipse([c - ri, c - ri, c + ri, c + ri], fill=(0, 0, 0, 0))
    mx, my = c + geo["mx"], c - geo["my"]
    if geo["m"] > 0:
        g = geo["g"]
        rd.ellipse([mx - g, my - g, mx + g, my + g], fill=(0, 0, 0, 0))
    img = Image.alpha_composite(img, ring)
    d = ImageDraw.Draw(img)
    p = geo["p"]
    d.ellipse([c - p, c - p, c + p, c + p], fill=rgb(ICON_INK) + (255,))
    if geo["m"] > 0:
        m = geo["m"]
        d.ellipse([mx - m, my - m, mx + m, my + m], fill=rgb(ICON_INK) + (255,))
    return img.resize((size, size), Image.LANCZOS)


def write(path, data):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    print("  ", os.path.relpath(path, ROOT))


def preview_sheet(path):
    sizes = [16, 24, 32, 48, 64, 128]
    W = sum(sizes) + 40 * (len(sizes) + 1)
    sheet = Image.new("RGBA", (W, 2 * (128 + 40)), rgb("#f4f4f2") + (255,))
    sheet.paste(Image.new("RGBA", (W, 128 + 40), (40, 44, 52, 255)), (0, 128 + 40))
    x = 40
    for n in sizes:
        ic = render_icon(n, "disc", 0.84)
        for row in (0, 1):
            sheet.alpha_composite(ic, (x, row * (128 + 40) + 20 + (128 - n) // 2))
        x += n + 40
    sheet.save(path)
    print("  ", os.path.relpath(path, ROOT))


def main(static=STATIC, here=HERE):
    print("O 높이 %d · O 획 %.0f → 고리 %.0f (×%.2f) · 위성 %s" % (oymax - oymin, O_STROKE, stroke(), RING, "있음" if MOON > 0 else "없음"))
    wm, ratio = build_wordmark()
    write(os.path.join(here, "wordmark-o.svg"), wm)          # §37.8 화면에서는 안 쓴다(마크 옆에 글자) — 마크가 O 인 판은 보관만
    write(os.path.join(static, "logo-mark.svg"), build_mark_svg())
    write(os.path.join(static, "logo-mark-clock.svg"), build_mark_svg_clock())   # §37.9 헤더·로그인에서 분침으로 돌린다
    write(os.path.join(static, "favicon.svg"), build_mark_svg(pad=stroke() * 1.2, backing=True).replace(INK, ICON_INK).replace(WHITE, ICON_BG))
    for n in (180, 192, 512):
        render_icon(n, "square", 0.62).save(os.path.join(static, "icon-%d.png" % n))
        print("   static/icon-%d.png" % n)
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [render_icon(n, "disc", 0.84) for n in ico_sizes]
    for target in (os.path.join(static, "horarium.ico"), os.path.join(here, "orbit.ico")):
        imgs[-1].save(target, format="ICO", sizes=[(n, n) for n in ico_sizes], append_images=imgs[:-1])
        print("  ", os.path.relpath(target, ROOT))
    preview_sheet(os.path.join(here, "orbit-preview.png"))


if __name__ == "__main__":
    main()
