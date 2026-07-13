# -*- coding: utf-8 -*-
"""回転テーブル修正: t32 → t25 (断面図寸法25が正、PL32は素材厚)."""
import sys, io, math, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from build123d import (BuildPart, Cylinder, CounterBoreHole, Hole, Location, Locations,
                       Align, export_step, export_gltf, import_step)
from OCP.BRepCheck import BRepCheck_Analyzer

OUT = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D化トライアル"
T = 25.0

with BuildPart() as p:
    Cylinder(220, T, align=(Align.CENTER, Align.CENTER, Align.MIN))
    locs = [Location((189 * math.cos(math.radians(30 * k)),
                      189 * math.sin(math.radians(30 * k)), T)) for k in range(12)]
    with Locations(*locs):
        CounterBoreHole(radius=7, counter_bore_radius=10, counter_bore_depth=13)
    locs = [Location((135 * math.cos(math.radians(22.5 + 45 * k)),
                      135 * math.sin(math.radians(22.5 + 45 * k)), T)) for k in range(8)]
    with Locations(*locs):
        Hole(radius=7)
    with Locations(Location((135, 0, T)), Location((-135, 0, T))):
        Hole(radius=8)

solid = p.part
disc = math.pi * 220**2 * T
exp = (disc - 12 * math.pi * 49 * T - 12 * math.pi * (100 - 49) * 13
       - 8 * math.pi * 49 * T - 2 * math.pi * 64 * T)
step = os.path.join(OUT, "15062-3-003_回転テーブル.step")
export_step(solid, step)
export_gltf(solid, os.path.join(OUT, "15062-3-003_回転テーブル.glb"), binary=True)
re = import_step(step).solids()
print(f"volume={solid.volume:,.0f} expected={exp:,.0f} diff={abs(solid.volume-exp)/exp*100:.2f}%")
print(f"BRepCheck={BRepCheck_Analyzer(solid.wrapped).IsValid()} reimport_ok={len(re)==1 and BRepCheck_Analyzer(re[0].wrapped).IsValid()}")
print("t=25 で再出力:", step)
