#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
胶片相机 NFC 挂件 —— 参数化建模脚本
=====================================

造型：35mm 片框风格。圆角矩形机身 + 上下两排齿孔浮雕 + 一端穿绳孔，
      中间埋一枚 25mm 圆片 NFC 标签（打印中途暂停放入，再封顶）。

坐标系：Z=0 是底面（直接贴热床），Z 向上。导出的 STL 可直接进切片器。

用法：
    python build_fob.py                 # 生成 fob.stl
    python build_fob.py --help          # 看全部参数
改尺寸：直接改下面 PARAMS 里的值，或者命令行覆盖，例如
    python build_fob.py --tag-d 26 --thickness 6 --out my-fob.stl

依赖：trimesh, shapely, manifold3d, numpy
"""

import argparse
import sys

import numpy as np
from shapely.geometry import Point, box
from shapely.ops import unary_union

import trimesh
from trimesh.boolean import difference
from trimesh.creation import extrude_polygon


# ══════════════════════════════════════════════════════════════
#  参数
# ══════════════════════════════════════════════════════════════

LAYER = 0.16          # 层高，用来反算暂停层号

PARAMS = dict(
    # ── 外形 ────────────────────────────────────────────────
    width          = 58.0,   # 总宽（X）
    height         = 38.0,   # 总高（Y）
    corner_r       = 7.0,    # 圆角半径

    # ── 厚度分层（单位 mm）─────────────────────────────────
    bottom_layers  = 8,      # 底面实心层数 → 0.16×8 = 1.28mm
    top_layers     = 14,     # 封顶层数      → 0.16×14 = 2.24mm

    # ── 标签腔 ──────────────────────────────────────────────
    tag_d          = 26.0,   # 腔体直径（25mm 圆片标签留 0.5mm 单边余量）
    gap_layers     = 9,      # 腔体深度 = 0.16×9 = 1.44mm（用层数定义，
                             #   保证「暂停高度」正好落在某一层结束处）

    # ── 穿绳孔 ──────────────────────────────────────────────
    hole_d         = 4.4,    # 孔径，4.4 能过常见 4mm 伞绳/挂绳
    hole_inset     = 5.5,    # 孔心距左边缘

    # ── 齿孔装饰（35mm 片框）──────────────────────────────
    perf           = True,   # 关掉就是一块干净的素面牌
    perf_w         = 2.8,    # 单个齿孔宽（X）
    perf_h         = 2.0,    # 单个齿孔高（Y）
    perf_pitch     = 4.74,   # 齿孔间距，35mm 标准
    perf_depth     = 1.2,    # 齿孔凹陷深度（上下各挖这么多，不挖穿）
    perf_inset     = 1.8,    # 齿孔外沿距上下边缘
)


def log(msg):
    print("[fob] " + msg)


# ══════════════════════════════════════════════════════════════
#  2D 轮廓
# ══════════════════════════════════════════════════════════════

def rounded_rect(w, h, r, qs=12):
    """圆心在原点、宽 w 高 h、四角半径 r 的圆角矩形。"""
    r = min(r, w / 2 - 0.01, h / 2 - 0.01)
    base = box(-w / 2, -h / 2, w / 2, h / 2)
    return base.buffer(-r, quad_segs=qs, join_style=1).buffer(r, quad_segs=qs, join_style=1)


def perf_field(P):
    """上下两排齿孔的水平投影。返回 (每个齿孔的矩形列表, 单排个数)。"""
    if not P["perf"]:
        return [], 0

    half_w = P["width"] / 2
    y_band = P["height"] / 2 - P["perf_inset"] - P["perf_h"] / 2

    xs, x = [], -half_w + 3.0
    while x <= half_w - 3.0:
        xs.append(x)
        x += P["perf_pitch"]

    holes = []
    for cx in xs:
        for cy in (y_band, -y_band):
            holes.append(box(cx - P["perf_w"] / 2, cy - P["perf_h"] / 2,
                             cx + P["perf_w"] / 2, cy + P["perf_h"] / 2))
    return holes, len(xs)


# ══════════════════════════════════════════════════════════════
#  建模
# ══════════════════════════════════════════════════════════════

def build(P):
    z_floor  = P["bottom_layers"] * LAYER              # 腔底
    z_pause  = z_floor + P["gap_layers"] * LAYER       # 腔口 = 暂停高度
    z_total  = z_pause + P["top_layers"] * LAYER       # 顶面

    # ── 外壳 2D 轮廓（已挖掉穿绳孔）──
    shell = rounded_rect(P["width"], P["height"], P["corner_r"])
    hole_x = -P["width"] / 2 + P["hole_inset"]
    shell = shell.difference(Point(hole_x, 0).buffer(P["hole_d"] / 2, quad_segs=32))

    body = extrude_polygon(shell, z_total)

    # ── 挖掉的部分 ──
    cutters = []

    # 标签腔：只从腔底挖到暂停高度，上方必须留出封顶
    cav = extrude_polygon(Point(0, 0).buffer(P["tag_d"] / 2, quad_segs=64),
                          (z_pause - z_floor) + 0.005)
    cav.apply_translation([0, 0, z_floor])
    cutters.append(cav)

    # 齿孔：上下两个面各挖浅坑，不挖穿
    holes, n_perf = perf_field(P)
    if holes:
        d = P["perf_depth"] + 0.5
        for r in holes:
            top = extrude_polygon(r, d)
            top.apply_translation([0, 0, z_total - P["perf_depth"]])
            cutters.append(top)
            bot = extrude_polygon(r, d)
            bot.apply_translation([0, 0, -0.5])
            cutters.append(bot)

    cutter = trimesh.util.concatenate(cutters)
    mesh = difference([body, cutter], engine="manifold")

    # ── 关键自检：标签正上方必须还有塑料（不然封顶被挖穿，标签会掉出来）──
    z_mid = (z_pause + z_total) / 2
    probes = np.array([
        [0, 0, z_mid],                    # 标签正中心上方
        [P["tag_d"] / 2 - 1.0, 0, z_mid], # 腔口边缘内侧上方
    ])
    solid_above = mesh.contains(probes)
    if not solid_above.all():
        raise RuntimeError("标签上方没有封顶（腔体挖穿了），检查 gap_layers / top_layers")

    # 腔体本身必须是空的
    if mesh.contains(np.array([[0, 0, z_floor + 0.3]]))[0]:
        raise RuntimeError("标签腔没有被挖出来，检查 tag_d 与 bottom_layers")

    meta = dict(z_floor=z_floor, z_pause=z_pause, z_total=z_total,
                gap=P["gap_layers"] * LAYER,
                pause_layer=P["bottom_layers"] + P["gap_layers"],
                n_perf=n_perf)
    return mesh, meta


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="生成胶片相机 NFC 挂件的 STL")
    for k, v in PARAMS.items():
        if isinstance(v, bool):
            ap.add_argument("--" + k.replace("_", "-"), type=lambda s: s.lower() in ("1", "true", "yes"), default=v)
        elif isinstance(v, int):
            ap.add_argument("--" + k.replace("_", "-"), type=int, default=v)
        else:
            ap.add_argument("--" + k.replace("_", "-"), type=float, default=v)
    ap.add_argument("--out", default="fob.stl", help="输出文件名")
    a = ap.parse_args()

    P = {k: getattr(a, k) for k in PARAMS}

    mesh, meta = build(P)

    if not mesh.is_watertight:
        log("警告：网格不是封闭的，切片可能出问题")

    mesh.export(a.out)

    b = mesh.bounds
    log("已写出 %s" % a.out)
    log("外形  %.1f × %.1f × %.2f mm" % (b[1][0] - b[0][0], b[1][1] - b[0][1], b[1][2] - b[0][2]))
    log("三角面 %d · 体积 %.2f cm³ · 约 %.1f g PLA" % (
        len(mesh.faces), mesh.volume / 1000.0, mesh.volume / 1000.0 * 1.24))
    log("齿孔 %d 个/排" % meta["n_perf"])
    log("腔底 Z = %.2f mm（第 %d 层）" % (meta["z_floor"], P["bottom_layers"]))
    log("腔深 %.2f mm，可放 1.0–1.4mm 厚的标签" % meta["gap"])
    log(">>> 在 Z = %.2f mm 处暂停（打完第 %d 层），放入标签后继续" % (
        meta["z_pause"], meta["pause_layer"]))
    log(">>> 标签上方封顶塑料 %.2f mm，顶面 Z = %.2f mm" % (
        P["top_layers"] * LAYER, meta["z_total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
