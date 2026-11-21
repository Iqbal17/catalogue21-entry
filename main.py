#!/usr/bin/env python3
"""The Toast Catapult - a catalogue photograph of an impossible gadget.

Renders a studio product shot (1024x1024 PNG) of a countertop toaster
with a spring-loaded catapult arm, caught mid-launch: a slice of toast
flies toward a waiting plate. Pure stdlib raster renderer: analytic
shapes, gradients, soft shadows, 2x supersampling, own PNG encoder.
Deterministic, no network.
"""

import math
import os
import struct
import zlib

SS = 2                      # supersampling factor
W = H = 1024                # final image size
SW = W * SS                 # working size

buf = [bytearray(SW * 3) for _ in range(SW)]


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return (lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t))


def blend(x, y, color, a):
    """Alpha-blend color (0-255 floats) into working pixel (x, y)."""
    if a <= 0.0 or x < 0 or y < 0 or x >= SW or y >= SW:
        return
    row = buf[y]
    i = x * 3
    if a >= 1.0:
        row[i] = int(color[0])
        row[i + 1] = int(color[1])
        row[i + 2] = int(color[2])
    else:
        ia = 1.0 - a
        row[i] = int(row[i] * ia + color[0] * a)
        row[i + 1] = int(row[i + 1] * ia + color[1] * a)
        row[i + 2] = int(row[i + 2] * ia + color[2] * a)


# ---------------------------------------------------------------- shapes
# All shape coordinates are given in final-image (1024) space.

def _bbox(x0, y0, x1, y1):
    return (max(0, int(x0 * SS) - 1), max(0, int(y0 * SS) - 1),
            min(SW - 1, int(x1 * SS) + 1), min(SW - 1, int(y1 * SS) + 1))


def fill_rrect(cx, cy, hw, hh, r, colorf):
    """Rounded rectangle; colorf(x, y) -> (r, g, b) in 1024 space."""
    xa, ya, xb, yb = _bbox(cx - hw - 1, cy - hh - 1, cx + hw + 1, cy + hh + 1)
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        qy = abs(py - cy) - (hh - r)
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            qx = abs(px - cx) - (hw - r)
            ax = qx if qx > 0.0 else 0.0
            ay = qy if qy > 0.0 else 0.0
            d = math.hypot(ax, ay) + min(max(qx, qy), 0.0) - r
            cov = clamp(0.5 - d * SS)
            if cov > 0.0:
                blend(xx, yy, colorf(px, py), cov)


def fill_circle(cx, cy, r, colorf):
    xa, ya, xb, yb = _bbox(cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1)
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        dy = py - cy
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            d = math.hypot(px - cx, dy) - r
            cov = clamp(0.5 - d * SS)
            if cov > 0.0:
                blend(xx, yy, colorf(px, py), cov)


def fill_ellipse(cx, cy, rx, ry, colorf, soft=0.0):
    """Ellipse; soft>0 gives a radial falloff (for shadows/glows)."""
    xa, ya, xb, yb = _bbox(cx - rx - 1, cy - ry - 1, cx + rx + 1, cy + ry + 1)
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        ny = (py - cy) / ry
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            nx = (px - cx) / rx
            t = math.hypot(nx, ny)
            if soft > 0.0:
                cov = clamp((1.0 - t) / soft)
                if cov > 0.0:
                    c = colorf(px, py)
                    blend(xx, yy, (c[0], c[1], c[2]), cov * clamp(c[3] if len(c) > 3 else 1.0))
            else:
                cov = clamp((1.0 - t) * min(rx, ry) * SS * 0.5 + 0.5)
                if cov > 0.0:
                    blend(xx, yy, colorf(px, py), cov)


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = clamp(((px - ax) * dx + (py - ay) * dy) / L2)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def stroke_segment(ax, ay, bx, by, width, color, alpha=1.0):
    hw = width / 2.0
    xa, ya, xb, yb = _bbox(min(ax, bx) - hw - 1, min(ay, by) - hw - 1,
                           max(ax, bx) + hw + 1, max(ay, by) + hw + 1)
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            d = _seg_dist(px, py, ax, ay, bx, by) - hw
            cov = clamp(0.5 - d * SS) * alpha
            if cov > 0.0:
                blend(xx, yy, color, cov)


def fill_poly(points, colorf):
    """Filled polygon with edge-distance anti-aliasing."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xa, ya, xb, yb = _bbox(min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            inside = False
            dmin = 1e9
            for (ax, ay), (bx, by) in edges:
                d = _seg_dist(px, py, ax, ay, bx, by)
                if d < dmin:
                    dmin = d
                if (ay > py) != (by > py):
                    xin = ax + (py - ay) * (bx - ax) / (by - ay)
                    if px < xin:
                        inside = not inside
            if inside:
                cov = clamp(0.5 + dmin * SS)
            else:
                cov = clamp(0.5 - dmin * SS)
            if cov > 0.0:
                blend(xx, yy, colorf(px, py), cov)


# ---------------------------------------------------------------- font
FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    " ": ("00000",) * 7,
}


def draw_text(cx, cy, text, px, color, alpha=1.0):
    """Centred blocky engraved text; px = pixel size of one font cell."""
    w = len(text) * 6 * px - px
    x0 = cx - w / 2.0
    y0 = cy - 7 * px / 2.0
    for ci, ch in enumerate(text):
        glyph = FONT[ch]
        gx = x0 + ci * 6 * px
        for ry, rowbits in enumerate(glyph):
            for rx, bit in enumerate(rowbits):
                if bit == "1":
                    xa, ya, xb, yb = _bbox(gx + rx * px, y0 + ry * px,
                                           gx + (rx + 1) * px, y0 + (ry + 1) * px)
                    for yy in range(ya, yb + 1):
                        for xx in range(xa, xb + 1):
                            blend(xx, yy, color, alpha)


# ---------------------------------------------------------------- backdrop

def backdrop():
    top = (216, 210, 200)
    mid = (198, 191, 180)
    floor = (226, 220, 210)
    floor_hi = (240, 235, 226)
    horizon = 712.0
    for yy in range(SW):
        py = (yy + 0.5) / SS
        if py < horizon:
            t = py / horizon
            c = mix(top, mid, t * t * 0.9 + t * 0.1)
        else:
            t = (py - horizon) / (H - horizon)
            c = mix(mid, floor, clamp(t * 1.6))
        row = buf[yy]
        for xx in range(SW):
            px = (xx + 0.5) / SS
            # soft radial studio glow centred behind the product
            dx = (px - 470) / 560.0
            dy = (py - 560) / 560.0
            g = clamp(1.0 - (dx * dx + dy * dy), 0.0, 1.0)
            r = c[0] + 26 * g
            gg = c[1] + 24 * g
            b = c[2] + 20 * g
            if py >= horizon:  # floor sheen
                fy = (py - horizon) / (H - horizon)
                s = clamp(1.0 - abs(px - 500) / 620.0) * (1.0 - fy) * 14
                r += s
                gg += s
                b += s
            # gentle vignette
            vx = abs(px - 512) / 512.0
            vy = abs(py - 512) / 512.0
            v = 1.0 - 0.10 * clamp(vx * vx + vy * vy - 0.55)
            i = xx * 3
            row[i] = int(clamp(r * v, 0, 255))
            row[i + 1] = int(clamp(gg * v, 0, 255))
            row[i + 2] = int(clamp(b * v, 0, 255))


# ---------------------------------------------------------------- materials

def cream_body(x, y):
    """Cream enamel with a soft top light and brushed vertical streaks."""
    t = clamp((y - 430) / 320.0)
    c = mix((247, 238, 219), (214, 196, 160), t * 0.85)
    streak = 3.0 * math.sin(x * 1.7) * math.sin(x * 0.23 + 1.3)
    hi = clamp(1.0 - abs(x - 300) / 190.0) * 10 * (1.0 - t)
    return (c[0] + streak + hi, c[1] + streak + hi, c[2] + streak * 0.6 + hi)


def chrome(x, y):
    t = clamp((y - 408) / 46.0)
    c = mix((228, 232, 237), (148, 155, 163), t)
    band = clamp(1.0 - abs(y - 424) / 5.0) * 38
    return (c[0] + band, c[1] + band, c[2] + band)


def steel(x, y):
    t = clamp((x - 300) / 260.0)
    c = mix((196, 201, 207), (138, 144, 152), t)
    return c


def soft_shadow(cx, cy, rx, ry, strength):
    fill_ellipse(cx, cy, rx, ry,
                 lambda x, y: (58, 50, 42, strength), soft=1.0)


# ---------------------------------------------------------------- plate

def plate():
    cx, cy = 762, 742
    # contact shadow
    soft_shadow(cx + 14, cy + 16, 128, 30, 0.34)
    # plate body: outer rim, inner well
    fill_ellipse(cx, cy, 122, 30, lambda x, y: mix((244, 242, 236), (203, 199, 190),
                                                    clamp((y - (cy - 30)) / 60.0)))
    fill_ellipse(cx, cy - 2, 118, 27, lambda x, y: (250, 248, 243))
    # blue rim stripe
    fill_ellipse(cx, cy - 2, 104, 22.5, lambda x, y: (62, 92, 138))
    fill_ellipse(cx, cy - 2, 97, 20.5, lambda x, y: (250, 248, 243))
    # empty well with a soft inner shading, waiting for the toast
    fill_ellipse(cx, cy - 1, 74, 15.5,
                 lambda x, y: mix((252, 250, 246), (228, 224, 214),
                                  clamp(math.hypot((x - cx) / 74.0, (y - cy) / 15.5))))


# ---------------------------------------------------------------- toaster

def toaster():
    bx, by = 330, 578          # body centre
    hw, hh = 148, 148          # body half extents (top 430, base 726)

    # floor shadows: broad ambient + tight contact
    soft_shadow(bx + 26, 738, 190, 30, 0.20)
    soft_shadow(bx + 10, 734, 158, 22, 0.34)

    # feet
    fill_rrect(bx - 108, 730, 26, 9, 4, lambda x, y: (74, 62, 50))
    fill_rrect(bx + 108, 730, 26, 9, 4, lambda x, y: (74, 62, 50))

    # body
    fill_rrect(bx, by, hw, hh, 34, cream_body)
    # left side shading + right edge shade for roundness
    fill_rrect(bx - hw + 13, by, 13, hh - 16, 10,
               lambda x, y: (255, 250, 238, 0.0) if False else (238, 226, 198))
    fill_rrect(bx + hw - 12, by, 12, hh - 14, 10, lambda x, y: (198, 178, 138))

    # chrome top rim
    fill_rrect(bx, 430, hw - 4, 24, 16, chrome)
    # top deck (slightly inset, darker steel) with two slots
    fill_rrect(bx, 424, hw - 26, 13, 8, lambda x, y: mix((172, 178, 185), (120, 126, 134),
                                                         clamp((y - 411) / 26.0)))
    fill_rrect(bx - 52, 424, 40, 6, 3, lambda x, y: (38, 33, 29))
    fill_rrect(bx + 52, 424, 40, 6, 3, lambda x, y: (38, 33, 29))

    # front panel seam
    fill_rrect(bx, 486, hw - 18, 1.6, 1.6, lambda x, y: (206, 188, 148))

    # browning dial (front left)
    fill_circle(bx - 88, 640, 30, lambda x, y: mix((214, 219, 224), (150, 156, 164),
                                                   clamp((y - 610) / 60.0)))
    fill_circle(bx - 88, 640, 22, lambda x, y: (240, 232, 210))
    stroke_segment(bx - 88, 640, bx - 88 + 13, 640 - 13, 5, (178, 52, 40))
    for ang in (0.6, 1.5, 2.4, 3.3, 4.2, 5.1):
        x1 = bx - 88 + 26 * math.cos(ang)
        y1 = 640 + 26 * math.sin(ang)
        stroke_segment(x1, y1, bx - 88 + 30 * math.cos(ang),
                       640 + 30 * math.sin(ang), 2.4, (120, 104, 78))

    # brand plate (front centre-right): brass plate, engraved text
    fill_rrect(bx + 42, 636, 74, 34, 6, lambda x, y: mix((196, 158, 84), (158, 120, 54),
                                                         clamp((y - 602) / 68.0)))
    fill_rrect(bx + 42, 636, 68, 28, 5, lambda x, y: (233, 214, 168))
    draw_text(bx + 42, 624, "CATAPULTA", 2.6, (74, 56, 26))
    draw_text(bx + 42, 652, "MODEL T-1", 2.2, (96, 74, 36))

    # lever slot + lever on the right side of the body
    fill_rrect(bx + hw - 4, 520, 5, 56, 2.5, lambda x, y: (52, 44, 36))
    fill_rrect(bx + hw + 16, 486, 20, 8, 4, lambda x, y: mix((170, 176, 182), (120, 126, 134),
                                                             clamp((x - bx - hw) / 40.0)))
    fill_circle(bx + hw + 34, 486, 13, lambda x, y: mix((96, 66, 44), (58, 38, 26),
                                                        clamp((y - 473) / 26.0)))


# ---------------------------------------------------------------- catapult

def catapult():
    px_, py_ = 268, 416          # pivot on the rear-left of the top deck
    ang = math.radians(-52)      # arm swung back, just released
    L = 168
    ex = px_ + L * math.cos(ang)
    ey = py_ + L * math.sin(ang)

    # rear support post + pivot housing
    fill_rrect(px_ - 4, 428, 13, 18, 5, steel)
    # counterweight tail of the arm
    tx = px_ - 44 * math.cos(ang)
    ty = py_ - 44 * math.sin(ang)
    stroke_segment(tx, ty, px_, py_, 13, (128, 134, 142))
    fill_circle(tx, ty, 13, lambda x, y: mix((150, 156, 164), (104, 110, 118),
                                             clamp((y - (ty - 13)) / 26.0)))
    # the arm
    stroke_segment(px_, py_, ex, ey, 11, (156, 162, 170))
    stroke_segment(px_, py_, ex, ey, 4.5, (206, 211, 217))
    # cup at the arm's end (open, toast just gone)
    cup_ang = ang + math.radians(90)
    c1 = (ex + 20 * math.cos(cup_ang), ey + 20 * math.sin(cup_ang))
    c2 = (ex - 20 * math.cos(cup_ang), ey - 20 * math.sin(cup_ang))
    stroke_segment(c1[0], c1[1], ex + 6 * math.cos(ang), ey + 6 * math.sin(ang), 7, (120, 126, 134))
    stroke_segment(c2[0], c2[1], ex + 6 * math.cos(ang), ey + 6 * math.sin(ang), 7, (120, 126, 134))
    stroke_segment(c1[0], c1[1], c2[0], c2[1], 5, (120, 126, 134))
    # pivot bolt
    fill_circle(px_, py_, 11, lambda x, y: mix((188, 193, 199), (120, 126, 134),
                                               clamp((y - (py_ - 11)) / 22.0)))
    fill_circle(px_, py_, 4.5, lambda x, y: (84, 88, 96))

    # coiled spring: front anchor on the deck to a point along the arm
    ax_, ay_ = 396, 428
    sx = px_ + 62 * math.cos(ang)
    sy = py_ + 62 * math.sin(ang)
    dx, dy = sx - ax_, sy - ay_
    dist = math.hypot(dx, dy)
    ux, uy = dx / dist, dy / dist
    vx, vy = -uy, ux
    steps = 30
    prev = None
    for i in range(steps + 1):
        t = i / steps
        off = 9.0 * math.sin(t * math.pi * 9) * (0.35 + 0.65 * math.sin(t * math.pi))
        cxp = ax_ + dx * t + vx * off
        cyp = ay_ + dy * t + vy * off
        if prev is not None:
            stroke_segment(prev[0], prev[1], cxp, cyp, 5.2, (110, 116, 124))
        prev = (cxp, cyp)
    fill_circle(ax_, ay_, 7, lambda x, y: (96, 101, 109))


# ---------------------------------------------------------------- toast

def _toast_local(px, py, cx, cy, ang, s):
    """Distance-ish test in toast-local space; returns (d, u, v)."""
    ca, sa = math.cos(-ang), math.sin(-ang)
    dx, dy = px - cx, py - cy
    u = (dx * ca - dy * sa) / s
    v = (dx * sa + dy * ca) / s
    # union of body rect and domed top
    qx = abs(u) - 34.0
    qy = max(v - 6.0, -(v + 44.0)) if v < 6.0 else 0.0
    rect_d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - 7.0
    circ_d = math.hypot(u, v - 6.0) - 41.0
    d = min(rect_d, circ_d) if v > -20 else rect_d
    return d, u, v


def toast(cx, cy, ang, s, alpha):
    xa, ya, xb, yb = _bbox(cx - 62 * s, cy - 62 * s, cx + 62 * s, cy + 62 * s)
    for yy in range(ya, yb + 1):
        py = (yy + 0.5) / SS
        for xx in range(xa, xb + 1):
            px = (xx + 0.5) / SS
            d, u, v = _toast_local(px, py, cx, cy, ang, s)
            cov = clamp(0.5 - d * SS / s) * alpha
            if cov <= 0.0:
                continue
            # crust ring, then crumb, then a toasted heart
            di, _, _ = _toast_local(px, py, cx, cy, ang, s * 0.86)
            if di > 0.0:
                c = mix((196, 122, 44), (158, 88, 26), clamp((v + 50) / 100.0))
            else:
                dj, _, _ = _toast_local(px, py, cx, cy, ang, s * 0.60)
                base = (243, 209, 141) if dj > 0.0 else (232, 183, 102)
                c = mix(base, (250, 224, 168), clamp(1.0 - (v + 50) / 110.0))
            blend(xx, yy, c, cov)


def toast_flight():
    # two faint earlier positions (motion ghosts), then the hero slice
    toast(560, 330, math.radians(-38), 0.94, 0.10)
    toast(612, 300, math.radians(-16), 0.97, 0.17)
    toast(672, 292, math.radians(14), 1.0, 1.0)
    # the flying slice casts a soft shadow far below, offset right
    soft_shadow(716, 748, 66, 10, 0.10)


# ---------------------------------------------------------------- output

def write_png(path):
    raw = bytearray()
    for yy in range(H):
        raw.append(0)
        r0 = buf[yy * SS]
        r1 = buf[yy * SS + 1]
        for xx in range(W):
            i0 = xx * SS * 3
            for ch in range(3):
                raw.append((r0[i0 + ch] + r0[i0 + 3 + ch] +
                            r1[i0 + ch] + r1[i0 + 3 + ch] + 2) // 4)

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    backdrop()
    plate()
    toaster()
    catapult()
    toast_flight()
    path = os.path.join(out_dir, "entry.png")
    n = write_png(path)
    print("wrote %s (%d bytes)" % (path, n))


if __name__ == "__main__":
    main()
