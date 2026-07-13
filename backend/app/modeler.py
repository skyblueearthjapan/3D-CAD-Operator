# -*- coding: utf-8 -*-
"""build123d による 3D モデリングと STEP / GLB 出力。

検出済みの Loop (厳密な line/arc/circle プリミティブの列) からワイヤを構築し、
穴付きの面を押し出してソリッドを作る。円弧は三点円弧で構築するため
トラバース方向の曖昧さがない。
"""
from __future__ import annotations

import math

from build123d import (
    Edge, Wire, Face, Plane, Vector,
    extrude, export_step, export_gltf,
)

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
    """
    if thickness <= 0:
        raise ModelError("板厚は正の値を指定してください")
    outer_wire = wire_from_loop(outer)
    hole_wires = []
    for h in holes:
        try:
            hole_wires.append(wire_from_loop(h))
        except ModelError:
            continue  # 不正な穴はスキップ
    try:
        face = Face(outer_wire, hole_wires)
    except Exception as e:
        raise ModelError(f"面の生成に失敗しました: {e}")
    if face.area <= 0:
        raise ModelError("面の面積が 0 です")

    if mode == "mid":
        solid = extrude(face, amount=thickness / 2, both=True)
    elif mode == "down":
        solid = extrude(face, amount=-thickness)
    else:
        solid = extrude(face, amount=thickness)
    return solid


def export_outputs(solid, step_path: str, glb_path: str) -> dict:
    export_step(solid, step_path)
    export_gltf(solid, glb_path, binary=True)
    bb = solid.bounding_box()
    return {
        "volume": round(solid.volume, 2),          # mm^3
        "bbox": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
    }
