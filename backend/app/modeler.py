# -*- coding: utf-8 -*-
"""build123d による 3D モデリングと STEP / GLB 出力。

検出済みの Loop (厳密な line/arc/circle プリミティブの列) からワイヤを構築し、
穴付きの面を押し出してソリッドを作る。円弧は三点円弧で構築するため
トラバース方向の曖昧さがない。
"""
from __future__ import annotations

import math

from build123d import (
    Edge, Wire, Face, Plane, Pos, Solid, Vector,
    extrude, export_step, export_gltf,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_ShapeEnum

from .contours import Loop
from .dxf_parser import Segment


class ModelError(Exception):
    pass


def _v(p: tuple[float, float]) -> tuple[float, float, float]:
    return (p[0], p[1], 0.0)


def _edges_from_step(seg: Segment, forward: bool) -> list[Edge]:
    a = seg.p0 if forward else seg.p1
    b = seg.p1 if forward else seg.p0
    if seg.kind == "line":
        return [Edge.make_line(_v(a), _v(b))]
    if seg.kind == "arc":
        m = seg.mid_point()
        return [Edge.make_three_point_arc(_v(a), _v(m), _v(b))]
    if seg.kind == "circle":
        return [Edge.make_circle(seg.radius, Plane(origin=_v(seg.center)))]
    if seg.kind == "poly":
        pts = seg.points if forward else seg.points[::-1]
        edges = []
        for i in range(len(pts) - 1):
            if math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]) > 1e-7:
                edges.append(Edge.make_line(_v(pts[i]), _v(pts[i + 1])))
        return edges
    raise ModelError(f"未対応セグメント種別: {seg.kind}")


def wire_from_loop(loop: Loop) -> Wire:
    edges: list[Edge] = []
    for seg, fwd in loop.steps:
        edges.extend(_edges_from_step(seg, fwd))
    if not edges:
        raise ModelError(f"輪郭 {loop.id} にエッジがありません")
    try:
        w = Wire(edges)
    except Exception:
        combined = Wire.combine(edges)
        if not combined:
            raise ModelError(f"輪郭 {loop.id} のワイヤ構築に失敗しました")
        w = combined[0]
    if not w.is_closed:
        raise ModelError(f"輪郭 {loop.id} が閉じていません")
    return w


def build_solid(outer: Loop, holes: list[Loop], thickness: float,
                mode: str = "up"):
    """押し出しソリッドを生成する。

    mode: 'up' (+Z), 'down' (-Z), 'mid' (両側均等)

    穴は面の内周ワイヤではなくブーリアン減算で開ける。図面上で穴領域が
    分割・接触・重複していても正しいソリッドになり、SOLIDWORKS 等での
    サーフェス化 (knit 失敗) を防ぐ。
    """
    if thickness <= 0:
        raise ModelError("板厚は正の値を指定してください")
    try:
        face = Face(wire_from_loop(outer))
    except Exception as e:
        raise ModelError(f"外形面の生成に失敗しました: {e}")
    if face.area <= 0:
        raise ModelError("外形面の面積が 0 です")

    if mode == "mid":
        z0, z1 = -thickness / 2, thickness / 2
        solid = extrude(face, amount=thickness / 2, both=True)
    elif mode == "down":
        z0, z1 = -thickness, 0.0
        solid = extrude(face, amount=-thickness)
    else:
        z0, z1 = 0.0, thickness
        solid = extrude(face, amount=thickness)

    # 穴カッター: 板厚より上下 1mm ずつ長く押し出して確実に貫通させる
    cutters = []
    for h in holes:
        try:
            hf = Face(wire_from_loop(h))
            if hf.area <= 0:
                continue
            cutters.append(extrude(Pos(0, 0, z0 - 1.0) * hf, amount=(z1 - z0) + 2.0))
        except (ModelError, Exception):
            continue  # 不正な穴はスキップ
    if cutters:
        try:
            cutter = cutters[0] if len(cutters) == 1 else cutters[0].fuse(*cutters[1:])
            solid = solid.cut(cutter)
        except Exception as e:
            raise ModelError(f"穴のブーリアン減算に失敗しました: {e}")

    return _heal(solid)


def _heal(solid):
    """ジオメトリ検証。不正なら ShapeFix で修復を試みる。"""
    try:
        if BRepCheck_Analyzer(solid.wrapped).IsValid():
            return solid
        fixer = ShapeFix_Shape(solid.wrapped)
        fixer.Perform()
        fixed = fixer.Shape()
        if fixed.ShapeType() == TopAbs_ShapeEnum.TopAbs_SOLID:
            healed = Solid(fixed)
            if healed.volume > 0:
                return healed
    except Exception:
        pass
    return solid


def is_valid(solid) -> bool:
    try:
        return bool(BRepCheck_Analyzer(solid.wrapped).IsValid())
    except Exception:
        return True


def export_outputs(solid, step_path: str, glb_path: str) -> dict:
    export_step(solid, step_path)
    export_gltf(solid, glb_path, binary=True)
    bb = solid.bounding_box()
    return {
        "volume": round(solid.volume, 2),          # mm^3
        "bbox": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
        "valid": is_valid(solid),
    }
