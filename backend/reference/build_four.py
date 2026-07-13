# -*- coding: utf-8 -*-
"""4部品を図面理解に基づいて3D化し、検証する。タップ穴は下穴径でモデル化。"""
import sys, io, math, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from build123d import (
    BuildPart, BuildSketch, Circle, Rectangle, extrude, Location, Locations,
    Plane, CounterBoreHole, Hole, chamfer, Mode, Align, Box, Cylinder,
    Rot, Pos, export_step, export_gltf, import_step,
)
from OCP.BRepCheck import BRepCheck_Analyzer

OUT = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D化トライアル"
os.makedirs(OUT, exist_ok=True)


def build_table():
    """15062-3-003 回転テーブル: φ440×32.
    12-φ14キリ ザグリφ20深13 PCD378等配 / 8-M16通し PCD270 (22.5°+45°k) / 2-φ16リーマ PCD270 0°,180°"""
    with BuildPart() as p:
        Cylinder(220, 32, align=(Align.CENTER, Align.CENTER, Align.MIN))
        locs = [Location((189 * math.cos(math.radians(30 * k)),
                          189 * math.sin(math.radians(30 * k)), 32)) for k in range(12)]
        with Locations(*locs):
            CounterBoreHole(radius=7, counter_bore_radius=10, counter_bore_depth=13)
        locs = [Location((135 * math.cos(math.radians(22.5 + 45 * k)),
                          135 * math.sin(math.radians(22.5 + 45 * k)), 32)) for k in range(8)]
        with Locations(*locs):
            Hole(radius=7)  # M16 下穴φ14
        with Locations(Location((135, 0, 32)), Location((-135, 0, 32))):
            Hole(radius=8)  # φ16リーマ
    disc = math.pi * 220**2 * 32
    exp = (disc - 12 * math.pi * 49 * 32 - 12 * math.pi * (100 - 49) * 13
           - 8 * math.pi * 49 * 32 - 2 * math.pi * 64 * 32)
    return p.part, exp


def build_lm_cover():
    """13063-S-019 LMカバー: 1830×50×2.3, 6-φ8 ピッチ362 端10, 縁8"""
    with BuildPart() as p:
        Box(1830, 50, 2.3, align=(Align.MIN, Align.MIN, Align.MIN))
        locs = [Location((10 + 362 * k, 50 - 8, 2.3)) for k in range(6)]
        with Locations(*locs):
            Hole(radius=4)
    exp = 1830 * 50 * 2.3 - 6 * math.pi * 16 * 2.3
    return p.part, exp


def build_stopper():
    """CSB-004 ストッパー: φ50×φ35×16, 半径方向M6通し×2 (90°, -30°)"""
    with BuildPart() as p:
        Cylinder(25, 16, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations(Location((0, 0, 0))):
            Hole(radius=17.5)
        for ang in (90, -30):
            # 半径方向M6 (下穴φ5): 外周から内穴まで片壁貫通
            d = math.radians(ang)
            cx, cy = 21.25 * math.cos(d), 21.25 * math.sin(d)
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Pos(cx, cy, 8) * Rot(0, 90, ang)):
                    Cylinder(2.5, 9.5)
    ring = math.pi * (25**2 - 17.5**2) * 16
    exp = ring - 2 * math.pi * 2.5**2 * 7.5  # 壁厚7.5の近似
    return p.part, exp


def build_dust_cover():
    """25152-S-14 防塵カバー: 天板1833×190×3.2 + 両側曲げ65(t3.2),
    右端14平坦張出し(フランジ無し・幅180), フランジ切欠き21幅@x156-177,
    天板ノッチ5深(左:21幅, 右:14幅), 左端フランジC20, PL4.5補強板50×180, 4-φ8"""
    T, W, L, H = 3.2, 190.0, 1833.0, 65.0
    FL = L - 14  # フランジは右端の14手前まで
    with BuildPart() as p:
        Box(L, W, T, align=(Align.MIN, Align.CENTER, Align.MIN))  # 天板 z0..3.2 (上面基準は後で)
        # 両側フランジ (下向き): z -61.8..0
        for s in (1, -1):
            with Locations(Location((0, s * (W / 2 - T / 2), -61.8))):
                Box(FL, T, 61.8, align=(Align.MIN, Align.CENTER, Align.MIN))
        # 左端フランジ下角 C20: x0..20, z-61.8..-41.8 を斜めカット
        with BuildPart(mode=Mode.SUBTRACT):
            with BuildSketch(Plane.XZ.offset(-W / 2 - 1)):
                with Locations((10, -55.13)):
                    Rectangle(20 * 1.5, 20 * 1.5, rotation=45)
            extrude(amount=2 * W + 2, both=False)
        # フランジ切欠き 21幅 @x156..177 (両側, 全深さ)
        with BuildPart(mode=Mode.SUBTRACT):
            with Locations(Location((156, -W / 2 - 1, -62)), Location((156, W / 2 - T - 1, -62))):
                Box(21, T + 2, 62, align=(Align.MIN, Align.MIN, Align.MIN))
        # 天板ノッチ 5深: 左 x156..177, 右 x1819..1833 (両縁)
        for x0, w in ((156, 21), (FL, 14)):
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Location((x0, W / 2 - 5, -0.5)), Location((x0, -W / 2, -0.5))):
                    Box(w, 5, T + 1, align=(Align.MIN, Align.MIN, Align.MIN))
        # 補強板 PL4.5: 50×180, x141.5..191.5, 天板上面(z3.2)に載る
        with Locations(Location((141.5, 0, T))):
            Box(50, 180, 4.5, align=(Align.MIN, Align.CENTER, Align.MIN))
        # 4-φ8: 左ペア x166.5 (補強板ごと貫通), 右ペア x1825
        with Locations(Location((166.5, 80, T + 4.5)), Location((166.5, -80, T + 4.5))):
            Hole(radius=4)
        with Locations(Location((1825, 80, T)), Location((1825, -80, T))):
            Hole(radius=4)
    top = L * W * T
    flanges = 2 * (FL * 61.8 * T)
    cham = 2 * (0.5 * 20 * 20 * T)
    cutouts = 2 * (21 * 61.8 * T)
    notches = 2 * (21 * 5 * T) + 2 * (14 * 5 * T)
    reinf = 50 * 180 * 4.5
    holes = 4 * math.pi * 16 * T + 2 * math.pi * 16 * 4.5
    exp = top + flanges - cham - cutouts - notches + reinf - holes
    return p.part, exp


BUILDS = [
    ("15062-3-003_回転テーブル", build_table),
    ("13063-S-019_LMカバー", build_lm_cover),
    ("CSB-004_ストッパー", build_stopper),
    ("25152-S-14_防塵カバー", build_dust_cover),
]

results = []
for name, fn in BUILDS:
    print("=" * 70)
    print(name)
    try:
        solid, exp = fn()
        vol = solid.volume
        valid = BRepCheck_Analyzer(solid.wrapped).IsValid()
        bb = solid.bounding_box()
        step = os.path.join(OUT, name + ".step")
        glb = os.path.join(OUT, name + ".glb")
        export_step(solid, step)
        export_gltf(solid, glb, binary=True)
        re = import_step(step).solids()
        re_ok = len(re) == 1 and BRepCheck_Analyzer(re[0].wrapped).IsValid()
        diff = abs(vol - exp) / exp * 100
        print(f"  volume={vol:,.0f}  expected={exp:,.0f}  diff={diff:.2f}%")
        print(f"  bbox={bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f}")
        print(f"  BRepCheck={valid}  reimport_ok={re_ok}  -> {step}")
        results.append((name, True))
    except Exception:
        traceback.print_exc()
        results.append((name, False))

print("\nSUMMARY:", ", ".join(f"{n}:{'OK' if ok else 'FAIL'}" for n, ok in results))
