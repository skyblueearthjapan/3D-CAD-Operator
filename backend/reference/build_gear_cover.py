# -*- coding: utf-8 -*-
"""25152-3-05 回転ギアフタ: 図面理解に基づく手動3Dモデリング + 検証.

形状 (図面から読解):
  - 円板 φ80 x t9 (SS400 PL9)
  - 中央 φ34 貫通穴
  - φ7 キリ穴 x2 @ PCD48 (Y軸上 ±24)
  - 皿ザグリ φ13.44 / 90° (上面=皿ネジ座面側)
  - 上面外周に C1 面取り
"""
import sys, io, math, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from build123d import (
    BuildPart, BuildSketch, Circle, extrude, Location, Locations, Plane,
    CounterSinkHole, Hole, chamfer, Axis, Mode, export_step, export_gltf, import_step,
)
from OCP.BRepCheck import BRepCheck_Analyzer

OUT_DIR = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D化トライアル"
os.makedirs(OUT_DIR, exist_ok=True)
STEP = os.path.join(OUT_DIR, "25152-3-05_ギアフタ.step")
GLB = os.path.join(OUT_DIR, "25152-3-05_ギアフタ.glb")

T = 9.0          # 板厚 PL9
OD = 80.0        # 外径 (主図 R=40)
CENTER_HOLE = 34.0
PCD = 48.0
DRILL = 7.0      # 7キリ
CSK_DIA = 13.44  # 皿ザグリ表面径 (DXF円 R=6.72 x2)
CSK_ANGLE = 90.0

with BuildPart() as p:
    with BuildSketch(Plane.XY):
        Circle(OD / 2)
        Circle(CENTER_HOLE / 2, mode=Mode.SUBTRACT)
    extrude(amount=T)
    # 上面(Z=T)の外周エッジに C1 面取り (図面: 皿側の面の外周コーナー)
    top_outer = [e for e in p.edges().filter_by(lambda e: e.geom_type.name == "CIRCLE")
                 if abs(e.bounding_box().max.Z - T) < 1e-6
                 and abs(e.radius - OD / 2) < 1e-6]
    chamfer(top_outer, length=1.0)
    # 皿ザグリ穴 x2 (上面から, PCD48 → Y=±24)
    with Locations(Location((0, PCD / 2, T)), Location((0, -PCD / 2, T))):
        CounterSinkHole(radius=DRILL / 2, counter_sink_radius=CSK_DIA / 2,
                        counter_sink_angle=CSK_ANGLE)

solid = p.part
vol = solid.volume
mass_g = vol * 7.85e-3  # SS400

# 理論体積 (円板 - 中心穴 - キリ穴 - 皿の増分 - 面取りリング)
disc = math.pi * (OD/2)**2 * T
c_hole = math.pi * (CENTER_HOLE/2)**2 * T
drills = 2 * math.pi * (DRILL/2)**2 * T
h_csk = (CSK_DIA - DRILL) / 2  # 90°皿の深さ
R, r = CSK_DIA/2, DRILL/2
frustum = math.pi * h_csk / 3 * (R*R + R*r + r*r) - math.pi * r*r * h_csk
ring = 2 * math.pi * (OD/2 - 0.5) * 0.5  # C1 面取り (パップスの定理近似)
expected = disc - c_hole - drills - 2*frustum - ring

print(f"volume        = {vol:,.0f} mm^3")
print(f"expected      = {expected:,.0f} mm^3  (diff {abs(vol-expected)/expected*100:.2f}%)")
print(f"mass (SS400)  = {mass_g:,.0f} g")
bb = solid.bounding_box()
print(f"bbox          = {bb.size.X:.2f} x {bb.size.Y:.2f} x {bb.size.Z:.2f}")
print(f"faces/edges   = {len(solid.faces())} / {len(solid.edges())}")
print(f"BRepCheck     = {BRepCheck_Analyzer(solid.wrapped).IsValid()}")

export_step(solid, STEP)
export_gltf(solid, GLB, binary=True)
print(f"\nSTEP -> {STEP}")
print(f"GLB  -> {GLB}")

# STEP 再インポート検証
re = import_step(STEP)
re_solids = re.solids()
print(f"\nre-import: solids={len(re_solids)} volume={re_solids[0].volume:,.0f} "
      f"valid={BRepCheck_Analyzer(re_solids[0].wrapped).IsValid()}")
