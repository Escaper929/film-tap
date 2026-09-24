#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 STL 生成尺寸图纸（俯视图 + 纵剖图），顺带做几何自检。

只用 numpy + shapely，不依赖 trimesh 的 path 模块。

用法：python preview.py [模型.stl]
输出：drawing.svg
"""

import sys
from collections import defaultdict

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union
import trimesh

# 相邻三角面在共享边上算出的交点会差 1e-16 量级，
# 不吸附的话圆环闭合不上，polygonize 会丢掉所有孔洞。
SNAP = 1e-4


def cut(mesh, origin, normal, u, v):
    """用平面切三角网格，返回该平面上「材料区域」的多边形（含孔洞）。"""
    o = np.asarray(origin, float)
    n = np.asarray(normal, float); n = n / np.linalg.norm(n)
    u = np.asarray(u, float); v = np.asarray(v, float)

    tris = np.asarray(mesh.triangles, float)
    d = (tris - o) @ n                       # 每个顶点到平面的有符号距离

    acc = defaultdict(list)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = d[:, a], d[:, b]
        idx = np.nonzero((da > 0) != (db > 0))[0]
        if not len(idx):
            continue
        t = da[idx] / (da[idx] - db[idx])
        P = tris[idx, a] + t[:, None] * (tris[idx, b] - tris[idx, a])
        for k, fi in enumerate(idx):
            acc[int(fi)].append(P[k])

    lines = []
    for pts in acc.values():
        if len(pts) != 2:
            continue
        p0, p1 = pts
        if np.linalg.norm(p1 - p0) < 1e-7:
            continue
        lines.append(LineString([
            (round(float(p0 @ u), 4), round(float(p0 @ v), 4)),
            (round(float(p1 @ u), 4), round(float(p1 @ v), 4)),
        ]))

    if not lines:
        return []

    faces = list(polygonize(unary_union(lines)))
    if not faces:
        return []

    # 按嵌套层数分奇偶：偶数为材料，奇数为孔。
    # 注意必须只拿「外环」去判包含——polygonize 已经把孔算进 faces 的
    # interiors 里了，直接 contains() 会因为代表点落在孔内而永远为假。
    shells = [Polygon(f.exterior.coords) for f in faces]
    depth = [
        sum(1 for j, s in enumerate(shells) if j != i and s.covers(f.representative_point()))
        for i, f in enumerate(faces)
    ]
    outer = unary_union([f for f, k in zip(faces, depth) if k % 2 == 0])
    inner = unary_union([f for f, k in zip(faces, depth) if k % 2 == 1])

    solid = outer.difference(inner) if not inner.is_empty else outer
    if solid.is_empty:
        return []
    if solid.geom_type == "MultiPolygon":
        return list(solid.geoms)
    return [solid]


def path_d(polys, sx, sy, ox, oy):
    """多边形列表 -> SVG path（evenodd，自动成孔）。"""
    parts = []
    for poly in polys:
        for ring in [poly.exterior] + list(poly.interiors):
            xy = np.asarray(ring.coords)
            pts = ["%.2f,%.2f" % (ox + x * sx, oy - y * sy) for x, y in xy]
            if len(pts) >= 4:
                parts.append("M" + "L".join(pts[:-1]) + "Z")
    return "".join(parts)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "fob.stl"
    m = trimesh.load(src)

    top = cut(m, [0, 0, 2.0], [0, 0, 1], [1, 0, 0], [0, 1, 0])

    a_top = sum(p.area for p in top)
    b = m.bounds
    w, h, t = b[1] - b[0]

    print("封闭网格 : %s" % m.is_watertight)
    print("三角面   : %d" % len(m.faces))
    print("外形     : %.2f × %.2f × %.2f mm" % (w, h, t))
    print("Z=2 截面 : %.1f mm²  孔洞 %d 个" % (a_top, sum(len(p.interiors) for p in top)))
    print("           （预期 2 个：穿绳孔 + 标签腔；齿孔在 Z=2 处已不存在）")

    # ── 施工图 ──
    TW, TH = 660, 500
    S = 9.0
    cx, cy = 330.0, 272.0
    x0, x1 = cx - w / 2 * S, cx + w / 2 * S
    y0, y1 = cy - h / 2 * S, cy + h / 2 * S

    def X(v): return cx + v * S
    def Y(v): return cy - v * S

    o = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%" '
        'font-family="system-ui,-apple-system,Segoe UI,sans-serif">' % (TW, TH),
        '<rect width="%d" height="%d" fill="#f7f3ec"/>' % (TW, TH),
        '<text x="30" y="34" font-size="15" font-weight="500" fill="#221d17">'
        '胶片相机 NFC 挂件 · 俯视图</text>',
        '<text x="30" y="53" font-size="11.5" fill="#6d6355">'
        '单位 mm · 圆角 R7 · 齿孔 2.8×2.0 @4.74 · 穿绳孔 Ø4.4 · 总厚 %.2f</text>' % t,

        # 主体
        '<path d="%s" fill="#c9bda8" fill-rule="evenodd" stroke="#6d6355" stroke-width="0.8"/>'
        % path_d(top, S, S, cx, cy),

        # 标签腔参考圆
        '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#b3601a" '
        'stroke-width="1" stroke-dasharray="5 3"/>' % (cx, cy, 12.5 * S),
        '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#b3601a" '
        'stroke-width="0.6" stroke-dasharray="2 3"/>' % (cx, cy, 12.5 * S + 0.5 * S),
        '<text x="%.1f" y="%.1f" font-size="11.5" fill="#b3601a" text-anchor="middle">'
        '腔 Ø26 · 深 1.44</text>' % (cx, cy - 4),
        '<text x="%.1f" y="%.1f" font-size="11.5" fill="#b3601a" text-anchor="middle">'
        '放 Ø25 标签</text>' % (cx, cy + 12),

        # 穿绳孔引出
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (X(-23.5), Y(0), X(-23.5) - 12, Y(0) - 46),
        '<text x="%.1f" y="%.1f" font-size="11" fill="#6d6355" text-anchor="middle">'
        'Ø4.4</text>' % (X(-23.5) - 16, Y(0) - 52),
    ]

    # 宽度标注
    o += [
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x0, y1 + 26, x1, y1 + 26),
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x0, y1 + 20, x0, y1 + 32),
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x1, y1 + 20, x1, y1 + 32),
        '<text x="%.1f" y="%.1f" font-size="11.5" fill="#444441" text-anchor="middle">%.0f</text>'
        % ((x0 + x1) / 2, y1 + 44, w),
        # 高度标注
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x1 + 26, y0, x1 + 26, y1),
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x1 + 20, y0, x1 + 32, y0),
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#8a7f6d" stroke-width="0.6"/>'
        % (x1 + 20, y1, x1 + 32, y1),
        '<text x="%.1f" y="%.1f" font-size="11.5" fill="#444441">%.0f</text>'
        % (x1 + 38, cy + 4, h),
    ]

    # 打印参数
    o += [
        '<rect x="30" y="%d" width="%d" height="86" rx="10" fill="#efe8dc"/>' % (TH - 108, TW - 60),
        '<text x="46" y="%d" font-size="12" font-weight="500" fill="#221d17">'
        '打印参数</text>' % (TH - 88),
        '<text x="46" y="%d" font-size="11.5" fill="#6d6355">'
        '层高 0.16 · 底面 8 层（Z=1.28）· 在 Z=2.72（第 17 层打完）暂停</text>' % (TH - 68),
        '<text x="46" y="%d" font-size="11.5" fill="#6d6355">'
        '放入 Ø25 圆片标签（厚 1.0–1.4）· 继续打完 14 层封顶 · 顶面 Z=4.96</text>' % (TH - 50),
        '<text x="46" y="%d" font-size="11.5" fill="#b3601a">'
        '标签上方剩 2.24mm 塑料 · 无需支撑 · 约 11.9g PLA</text>' % (TH - 32),
    ]

    o.append("</svg>")

    with open("drawing.svg", "w", encoding="utf-8") as f:
        f.write("\n".join(o))
    print("已写出 drawing.svg")


def _count_pockets(polys):
    """统计被外轮廓包住的独立孔洞数量。"""
    if not polys:
        return 0
    outer = max(polys, key=lambda p: p.area)
    return sum(1 for p in polys if p is not outer and outer.contains(p.representative_point()))


if __name__ == "__main__":
    main()
