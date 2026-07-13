# -*- coding: utf-8 -*-
"""難易度高めの3部品: ドグ(カムプロファイル) / ベアリングケース(段付き旋盤物) / 旋回ギア(インボリュート歯形生成)."""
import sys, io, math, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from build123d import (
    BuildPart, BuildSketch, Polygon, Circle, extrude, Location, Locations, Plane,
    Cylinder, Box, Hole, Mode, Align, Pos, Rot, export_step, export_gltf, import_step,
)
from OCP.BRepCheck import BRepCheck_Analyzer

OUT = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D化トライアル"
os.makedirs(OUT, exist_ok=True)
CCC = (Align.CENTER, Align.CENTER, Align.MIN)


def shoelace(pts):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def build_dog():
    """25152-S-11 オーバーランドグ: PL12, プロファイル110x25(45°ランプ), 2-φ8 @x10/100 ベース部貫通"""
    prof = [(110, 0), (110, 5), (90, 5), (70, 25), (40, 25), (20, 5), (0, 5), (0, 0)]
    with BuildPart() as p:
        with BuildSketch(Plane.XZ):  # X=長さ, Z(sketch-y)=高さ, 押出=奥行き(板厚)
            Polygon(*prof, align=None)
        extrude(amount=12)
        # φ8×2: ベース(高さ5)を上から貫通 (図は φ7 で描画されているが注記 2-φ8 を採用)
        for x in (10, 100):
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Pos(x, -6, 2.5) * Rot(90, 0, 0)):
                    Cylinder(4, 7)
    exp = shoelace(prof) * 12 - 2 * math.pi * 16 * 5
    return p.part, exp, 0.5


def build_bearing_case():
    """22129-P1-05 ベアリングケース: φ198xt15フランジ + φ140x74胴 + φ115x7インロー,
    内径 上からφ90x30 → φ80x54 → φ67x3 → φ55x9 貫通, 4-φ14 @PCD170"""
    with BuildPart() as p:
        Cylinder(115 / 2, 7, align=CCC)                              # インロー z0..7
        with Locations(Location((0, 0, 7))):
            Cylinder(198 / 2, 15, align=CCC)                         # フランジ z7..22
        with Locations(Location((0, 0, 22))):
            Cylinder(140 / 2, 74, align=CCC)                         # 胴 z22..96
        # 内径段付き (上面 z=96 から)
        for r, z0, h in ((45, 66, 31), (40, 12, 54), (33.5, 9, 3), (27.5, -1, 11)):
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Location((0, 0, z0))):
                    Cylinder(r, h, align=CCC)
        # 4-φ14 フランジ貫通 @PCD170, 45°位置
        for k in range(4):
            a = math.radians(45 + 90 * k)
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Location((85 * math.cos(a), 85 * math.sin(a), 6))):
                    Cylinder(7, 17, align=CCC)
    exp = (math.pi * (57.5**2 * 7 + 99**2 * 15 + 70**2 * 74)
           - math.pi * (45**2 * 30 + 40**2 * 54 + 33.5**2 * 3 + 27.5**2 * 9)
           - 4 * math.pi * 49 * 15)
    return p.part, exp, 1.0


def gear_outline(m, z, ra, rf, alpha_deg=20.0, n_flank=14, n_arc=4):
    """標準インボリュート平歯車の外形ポリゴン(全歯)を生成"""
    rp = m * z / 2
    rb = rp * math.cos(math.radians(alpha_deg))
    inv = lambda phi: math.tan(phi) - phi
    alpha = math.radians(alpha_deg)
    half_p = math.pi / (2 * z)  # ピッチ円での歯厚半角

    def half_angle(r):  # 半径rでの歯厚半角
        phi = math.acos(rb / r)
        return half_p + inv(alpha) - inv(phi)

    rs = [max(rb, rf) + (ra - max(rb, rf)) * i / (n_flank - 1) for i in range(n_flank)]
    pitch = 2 * math.pi / z
    pts = []
    for k in range(z):
        tc = k * pitch
        ha_b = half_angle(max(rb, rf) + 1e-9)
        # 歯底円弧 (前の歯の右フランクからこの歯の左フランクまで)
        a0 = tc - pitch + ha_b
        a1 = tc - ha_b
        for i in range(1, n_arc + 1):
            a = a0 + (a1 - a0) * i / (n_arc + 1)
            pts.append((rf * math.cos(a), rf * math.sin(a)))
        if rf < rb:  # 歯元の径方向部
            pts.append((rf * math.cos(tc - ha_b), rf * math.sin(tc - ha_b)))
        for r in rs:  # 左フランク(上り)
            a = tc - half_angle(r)
            pts.append((r * math.cos(a), r * math.sin(a)))
        ha_a = half_angle(ra)
        for i in range(1, n_arc):  # 歯先円弧
            a = tc - ha_a + 2 * ha_a * i / n_arc
            pts.append((ra * math.cos(a), ra * math.sin(a)))
        for r in reversed(rs):  # 右フランク(下り)
            a = tc + half_angle(r)
            pts.append((r * math.cos(a), r * math.sin(a)))
        if rf < rb:
            pts.append((rf * math.cos(tc + ha_b), rf * math.sin(tc + ha_b)))
    return pts


def build_gear():
    """25152-P2-04 旋回ギア: m5 z27 α20°, OD145/root122.5, 歯幅57 + ハブφ96x24(全長81),
    穴φ65 + キー溝18 x 高さ69.4, M6ホーロー2本(0°=キー上, 120°) 右端から12"""
    pts = gear_outline(m=5, z=27, ra=72.5, rf=61.25)
    with BuildPart() as p:
        with BuildSketch(Plane.XY):
            Polygon(*pts, align=None)
        extrude(amount=57)                                    # 歯部 z0..57
        with Locations(Location((0, 0, 57))):
            Cylinder(48, 24, align=CCC)                       # ハブ z57..81
        with BuildPart(mode=Mode.SUBTRACT):
            with Locations(Location((0, 0, -1))):
                Cylinder(32.5, 83, align=CCC)                 # 穴φ65
            with Locations(Location((0, -9, -1))):
                Box(34.7, 18, 83, align=(Align.MIN, Align.MIN, Align.MIN))  # キー溝 +X向き
        for ang in (0, 120):                                  # M6ホーロー下穴φ5 半径方向
            a = math.radians(ang)
            cx, cy = 40 * math.cos(a), 40 * math.sin(a)
            with BuildPart(mode=Mode.SUBTRACT):
                with Locations(Pos(cx, cy, 69) * Rot(0, 90, ang)):
                    Cylinder(2.5, 18)
    # 期待体積: 歯形面積(shoelace) + ハブ - 穴 - キー溝増分 - 止めねじ(概算)
    teeth_area = shoelace(pts)
    n = 400
    kw_extra = sum((34.7 - math.sqrt(32.5**2 - y * y)) * (18 / n)
                   for y in [(-9 + 18 * (i + 0.5) / n) for i in range(n)])
    exp = (teeth_area * 57 + math.pi * 48**2 * 24 - math.pi * 32.5**2 * 81
           - kw_extra * 81 - 2 * math.pi * 2.5**2 * 15.5)
    return p.part, exp, 1.5


BUILDS = [
    ("25152-S-11_ドグ", build_dog),
    ("22129-P1-05_ベアリングケース", build_bearing_case),
    ("25152-P2-04_旋回ギアm5-27T", build_gear),
]

for name, fn in BUILDS:
    print("=" * 70)
    print(name)
    try:
        solid, exp, tol = fn()
        vol = solid.volume
        valid = BRepCheck_Analyzer(solid.wrapped).IsValid()
        bb = solid.bounding_box()
        step = os.path.join(OUT, name + ".step")
        export_step(solid, step)
        export_gltf(solid, os.path.join(OUT, name + ".glb"), binary=True)
        re = import_step(step).solids()
        re_ok = len(re) == 1 and BRepCheck_Analyzer(re[0].wrapped).IsValid()
        diff = abs(vol - exp) / exp * 100
        print(f"  volume={vol:,.0f}  expected={exp:,.0f}  diff={diff:.2f}% (許容{tol}%)")
        print(f"  bbox={bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f}")
        print(f"  BRepCheck={valid}  reimport_ok={re_ok}  -> {step}")
    except Exception:
        traceback.print_exc()
