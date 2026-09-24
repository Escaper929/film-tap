#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 fob.stl 渲染成预览图（纯 numpy + Pillow，不依赖 OpenGL）。
输出 preview.png，含三个视角：
  ① 正面等轴测   ② 揭掉封顶层看内部腔体   ③ 底面

用法：python render.py [模型.stl]
"""

import os
import sys

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

BG      = (247, 243, 236)


def cjk_font(size):
    """找一个能渲染中文的系统字体，找不到就退回默认位图字体。"""
    for p in (r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/msyhl.ttc",
              r"C:/Windows/Fonts/simhei.ttf", r"C:/Windows/Fonts/simsun.ttc",
              "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

PLA     = np.array([206, 197, 180], float)   # 本体色（米白 PLA）
PLA_PRO = np.array([196, 186, 167], float)   # 齿孔底面稍暗
TAG     = np.array([214, 132,  52], float)   # 标签示意色（琥珀）
EDGE    = np.array([120, 110, 96],  float)


def basis(az_deg, el_deg):
    """返回 (右, 上, 朝向相机) 三个正交单位向量。"""
    az, el = np.radians(az_deg), np.radians(el_deg)
    d = np.array([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)])
    right = np.cross(np.array([0.0, 0.0, 1.0]), d)
    right /= np.linalg.norm(right)
    up = np.cross(d, right)
    return right, up, d


def render_panel(tris, az, el, size, scale, center, cull=True, tag_disc=None):
    """画一个视角，返回 RGBA numpy 数组。"""
    W, H = size
    ss = 2                                   # 超采样
    W2, H2 = W * ss, H * ss
    img = Image.new("RGB", (W2, H2), BG)
    dr = ImageDraw.Draw(img)

    right, up, d = basis(az, el)
    light = np.array([0.45, -0.35, 0.82])
    light /= np.linalg.norm(light)

    pts = np.asarray(center, float)

    def project(V):
        c = V - pts
        return (c @ right) * scale * ss + W2 / 2, -(c @ up) * scale * ss + H2 / 2

    order, draw_list = [], []
    for f in tris:
        n = np.cross(f[1] - f[0], f[2] - f[0])
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        n = n / ln
        if cull and n @ d <= 0.02:
            continue
        depth = float(np.mean(f @ d))
        # 平面着色 + 一点点环境光
        lam = max(0.0, float(n @ light))
        shade = 0.34 + 0.66 * lam
        col = np.clip(PLA * shade, 0, 255)
        x, y = project(f)
        order.append(depth)
        draw_list.append((depth, list(zip(x, y)), col))

    draw_list.sort(key=lambda t: t[0])
    for _, poly, col in draw_list:
        dr.polygon(poly, fill=tuple(int(v) for v in col))

    # 标签示意圆盘：封顶层已揭掉，腔体上方没有遮挡，所以最后画即可
    if tag_disc is not None:
        cz, r = tag_disc
        th = np.linspace(0, 2 * np.pi, 128)
        for zz, rr, col in ((cz, r, TAG * 0.72), (cz + 1.15, r * 0.94, TAG)):
            ring = np.stack([rr * np.cos(th), rr * np.sin(th), np.full_like(th, zz)], 1)
            x, y = project(ring)
            dr.polygon(list(zip(x, y)), fill=tuple(int(v) for v in np.clip(col, 0, 255)))

    return np.asarray(img.resize((W, H), Image.LANCZOS))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "fob.stl"
    m = trimesh.load(src)
    tris = np.asarray(m.triangles, float)

    b = m.bounds
    center = (b[0] + b[1]) / 2

    # 只保留局部坐标，便于投影
    def shift(t):
        return t - center

    ctr = np.zeros(3)

    # ── 面板 ①：正面等轴测（完整）──
    p1 = render_panel([shift(t) for t in tris], 32, 34, (520, 360), 6.2, ctr)

    # ── 面板 ②：揭掉 Z>2.72 的所有面，露出标签腔 ──
    cut_tris = [shift(t) for t in tris if np.mean(t[:, 2], axis=0) <= 2.72 + 1e-6]
    p2 = render_panel(cut_tris, 32, 46, (520, 360), 6.2, ctr, tag_disc=(1.9, 12.3))

    # ── 面板 ③：底面等轴测 ──
    p3 = render_panel([shift(t) for t in tris], 32, -32, (520, 360), 6.2, ctr)

    W, H = 1660, 486
    out = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(out)
    font = cjk_font(16)
    for i, (panel, cap) in enumerate((
        (p1, "① 正面等轴测    58 × 38 × 4.96 mm"),
        (p2, "② 揭掉封顶层    内部 Ø26 标签腔"),
        (p3, "③ 底面视图    齿孔为浮雕，不挖穿"),
    )):
        x = 20 + i * 546
        out.paste(Image.fromarray(panel), (x, 16))
        dr.text((x + 6, 392), cap, fill=(70, 64, 55), font=font)

    out.save("preview.png")
    print("已写出 preview.png  %d × %d" % out.size)


if __name__ == "__main__":
    main()
