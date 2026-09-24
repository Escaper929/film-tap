// ═══════════════════════════════════════════════════════════════
//  胶片相机 NFC 挂件 —— OpenSCAD 参数版
// ═══════════════════════════════════════════════════════════════
//  和 build_fob.py 是同一个模型，这个版本方便你：
//    · 改尺寸后立刻预览
//    · 用 text() 在正面压出相机名（Python 版做不到）
//    · 导出 STL 丢给切片器
//
//  打印要点：
//    0.16mm 层高，打印到 Z = 2.72mm（第 17 层）设暂停，
//    放入 25mm 圆片标签，继续打印封顶。
//    标签上方剩 2.24mm 塑料，NFC 穿透毫无压力。
// ═══════════════════════════════════════════════════════════════

LAYER      = 0.16;

// ── 外形 ──
W          = 58;      // 总宽
H          = 38;      // 总高
R          = 7;       // 圆角半径

// ── 厚度分层（用层数定义，保证暂停落在整层）──
BOT_LAYERS = 8;       // 底面实心层数
GAP_LAYERS = 9;       // 标签腔深度层数
TOP_LAYERS = 14;      // 封顶层数

// ── 标签腔 ──
TAG_D      = 26;      // 腔体直径（25mm 标签留单边 0.5mm 余量）

// ── 穿绳孔 ──
HOLE_D     = 4.4;
HOLE_INSET = 5.5;     // 孔心距左边缘

// ── 齿孔装饰（35mm 片框）──
PERF       = true;
PERF_W     = 2.8;
PERF_H     = 2.0;
PERF_PITCH = 4.74;    // 35mm 标准齿孔间距
PERF_DEPTH = 1.2;
PERF_INSET = 1.8;     // 齿孔外沿距上下边缘

// ── 正面浮雕文字（可选，留空则不打）──
NAME       = "";      // 例如 "M6"、"FM2"、"XA2"
NAME_SIZE  = 6;
NAME_DEPTH = 0.7;

$fn = 64;

// ── 派生尺寸 ──
Z_FLOOR = BOT_LAYERS * LAYER;
Z_PAUSE = Z_FLOOR + GAP_LAYERS * LAYER;
Z_TOTAL = Z_PAUSE + TOP_LAYERS * LAYER;

echo(str("总厚 ", Z_TOTAL, " mm"));
echo(str(">>> 在 Z = ", Z_PAUSE, " mm 暂停（第 ", BOT_LAYERS + GAP_LAYERS, " 层）"));

module rrect(w, h, r) {
    offset(r = r) square([w - 2*r, h - 2*r], center = true);
}

module perf_row(sign) {
    translate([0, sign * (H/2 - PERF_INSET - PERF_H/2)])
        for (x = [-W/2 + 3 : PERF_PITCH : W/2 - 3])
            translate([x, 0]) square([PERF_W, PERF_H], center = true);
}

module fob() {
    difference() {
        // 主体
        linear_extrude(Z_TOTAL)
            difference() {
                rrect(W, H, R);
                translate([-W/2 + HOLE_INSET, 0]) circle(d = HOLE_D);
            }

        // 标签腔（只挖到暂停高度，上方留封顶，不然标签会掉出来）
        translate([0, 0, Z_FLOOR])
            cylinder(d = TAG_D, h = Z_PAUSE - Z_FLOOR + 0.005);

        // 齿孔（上下各挖浅坑，不挖穿）
        if (PERF) {
            translate([0, 0, -0.5])
                linear_extrude(PERF_DEPTH + 0.5) perf_row(-1);
            translate([0, 0, Z_TOTAL - PERF_DEPTH])
                linear_extrude(PERF_DEPTH + 0.5) perf_row(1);
        }

        // 正面浮雕文字
        if (NAME != "")
            translate([0, 0, Z_TOTAL - NAME_DEPTH])
                linear_extrude(NAME_DEPTH + 0.5)
                    text(NAME, size = NAME_SIZE, halign = "center",
                         valign = "center", font = "Arial:style=Bold");
    }
}

fob();
