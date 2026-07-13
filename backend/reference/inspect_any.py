# -*- coding: utf-8 -*-
"""Generic DXF inspector: compact geometry + notes summary for shape interpretation."""
import sys, io, math
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import ezdxf

def rnd(v, n=2):
    return round(v, n)

def inspect(path):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    print("=" * 100)
    print("FILE:", path.split("\\")[-1])
    cnt = Counter((e.dxftype(), e.dxf.layer, e.dxf.linetype) for e in msp)
    print("-- counts (type, layer, linetype):")
    for (t, layer, lt), n in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"   {t:12s} L={layer:8s} lt={lt:12s} x{n}")

    print("-- CIRCLE:")
    for e in msp.query("CIRCLE"):
        c = e.dxf.center
        print(f"   ({rnd(c.x):9},{rnd(c.y):9}) R={rnd(e.dxf.radius,3):8} lt={e.dxf.linetype} L={e.dxf.layer}")

    arcs = list(msp.query("ARC"))
    print(f"-- ARC ({len(arcs)}):")
    for e in arcs[:40]:
        c = e.dxf.center
        print(f"   ({rnd(c.x):9},{rnd(c.y):9}) R={rnd(e.dxf.radius,3):8} "
              f"a={rnd(e.dxf.start_angle,1)}..{rnd(e.dxf.end_angle,1)} lt={e.dxf.linetype} L={e.dxf.layer}")

    print("-- LWPOLYLINE:")
    for e in msp.query("LWPOLYLINE"):
        pts = list(e.get_points())
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        has_bulge = any(abs(p[4]) > 1e-9 for p in pts)
        print(f"   n={len(pts):3d} closed={e.closed} bulge={has_bulge} lt={e.dxf.linetype} L={e.dxf.layer} "
              f"bbox=({rnd(min(xs),1)},{rnd(min(ys),1)})-({rnd(max(xs),1)},{rnd(max(ys),1)})")

    lines = list(msp.query("LINE"))
    print(f"-- LINE ({len(lines)}) non-continuous or short list:")
    shown = 0
    for e in lines:
        if shown >= 300:
            print("   ...")
            break
        s, t = e.dxf.start, e.dxf.end
        print(f"   ({rnd(s.x,1):8},{rnd(s.y,1):8})-({rnd(t.x,1):8},{rnd(t.y,1):8}) lt={e.dxf.linetype} L={e.dxf.layer}")
        shown += 1

    print("-- NOTES:")
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT"):
            raw = (e.dxf.text if e.dxftype() == "TEXT" else e.text).replace("\n", "|")
            import re
            clean = re.sub(r"\\[A-Za-z][0-9.]*;?|[{}]|\\P", "|", raw).strip("| ")
            if clean and len(clean) > 0:
                print(f"   {clean[:80]}")

    print("-- DIMENSION:")
    for e in msp.query("DIMENSION"):
        try:
            m = rnd(e.get_measurement(), 3)
        except Exception:
            m = "?"
        tm = e.dxf.text_midpoint
        print(f"   dimtype={e.dimtype} m={m} text='{e.dxf.text}' at({rnd(tm.x,0)},{rnd(tm.y,0)})")

for p in sys.argv[1:]:
    try:
        inspect(p)
    except Exception as ex:
        print("ERROR", p, ex)
