# -*- coding: utf-8 -*-
"""円弧曲げ板金 (rolled_plate) ビルダー。

R曲げされた板金カバー = 円筒殻セクタ (annular-sector を軸方向に押し出したもの)。
任意で両端に接線方向の直線 (フラット) 延長を付けられる。

設計:
  断面 (XY平面) = 内半径 r_in・外半径 r_out・開き角 arc_angle_deg の環状セクタ。
  これを Z 方向 (軸方向) に width だけ押し出して円筒殻セクタを得る。
  両端の直線延長 straight_ends=[len_start, len_end] は、各アーク端に接線方向へ
  伸びる平板 (長さ×板厚×幅の直方体) として断面に合成する。

半径基準 radius_ref:
  "inner"  : arc_radius = 内半径 (既定)。r_out = arc_radius + thickness
  "outer"  : arc_radius = 外半径。       r_in  = arc_radius - thickness
  "neutral": arc_radius = 中立面半径。    r_in=arc_radius-t/2, r_out=arc_radius+t/2

理論体積 (検証用):
  円筒殻セクタ = angle_rad * (r_out^2 - r_in^2) / 2 * width
  直線延長     = len * thickness * width  (平板)

build123d の罠回避: 減算は使わずセクタ断面を直接作図して押し出すため入れ子 SUBTRACT は不要。
穴 (曲面上のドリル) はこのビルダーでは扱わない (unmodeled_features 相当。理由は REPORT 参照)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RolledParams:
    """円弧曲げ板金のパラメータ。"""
    arc_radius: float                                   # 基準半径 (radius_ref で意味が変わる)
    thickness: float                                    # 板厚
    arc_angle_deg: float                                # 曲げの開き角 (度)
    width: float                                        # 軸方向の幅
    radius_ref: Literal["inner", "outer", "neutral"] = "inner"
    straight_ends: list[float] = field(default_factory=lambda: [0.0, 0.0])  # [始端, 終端] の直線延長長さ
    start_angle_deg: float = 90.0                       # 断面アークの開始角 (既定=上向き対称配置の基準)

    def radii(self) -> tuple[float, float]:
        """(r_in, r_out) を返す。"""
        t = self.thickness
        if self.radius_ref == "inner":
            return self.arc_radius, self.arc_radius + t
        if self.radius_ref == "outer":
            return self.arc_radius - t, self.arc_radius
        return self.arc_radius - t / 2.0, self.arc_radius + t / 2.0


def theoretical_volume(p: RolledParams) -> float:
    """理論体積 mm^3 (円筒殻セクタ + 直線延長平板)。"""
    r_in, r_out = p.radii()
    ang = math.radians(p.arc_angle_deg)
    shell = ang * (r_out ** 2 - r_in ** 2) / 2.0 * p.width
    flat = sum(max(0.0, L) for L in p.straight_ends) * p.thickness * p.width
    return shell + flat


def _sector_face_wire(r_in: float, r_out: float, a0_deg: float, ang_deg: float,
                      straight_ends: list[float]):
    """環状セクタ (+接線延長) の閉断面を BuildSketch 内で作図し face 化する。"""
    from build123d import (BuildLine, BuildSketch, CenterArc, Line, Plane,
                           Polygon, make_face, Mode)

    a0 = math.radians(a0_deg)
    a1 = math.radians(a0_deg + ang_deg)

    def pt(r, a):
        return (r * math.cos(a), r * math.sin(a))

    with BuildSketch(Plane.XY) as sk:
        with BuildLine():
            # 外アーク a0->a1, 終端で内へ, 内アーク a1->a0, 始端で閉じる
            CenterArc((0, 0), r_out, a0_deg, ang_deg)
            Line(pt(r_out, a1), pt(r_in, a1))
            CenterArc((0, 0), r_in, a0_deg + ang_deg, -ang_deg)
            Line(pt(r_in, a0), pt(r_out, a0))
        make_face()

        # 接線方向の直線延長 (両端)。断面上で平板矩形として和結合。
        le_start = straight_ends[0] if len(straight_ends) > 0 else 0.0
        le_end = straight_ends[1] if len(straight_ends) > 1 else 0.0
        if le_end and le_end > 0:
            # 終端 a1 の接線方向 (CCW): (-sin a1, cos a1)
            tx, ty = -math.sin(a1), math.cos(a1)
            pi, po = pt(r_in, a1), pt(r_out, a1)
            corners = [pi, po,
                       (po[0] + le_end * tx, po[1] + le_end * ty),
                       (pi[0] + le_end * tx, pi[1] + le_end * ty)]
            Polygon(*corners, align=None, mode=Mode.ADD)
        if le_start and le_start > 0:
            # 始端 a0 の接線方向 (外向き=CCWの逆): (sin a0, -cos a0)
            tx, ty = math.sin(a0), -math.cos(a0)
            pi, po = pt(r_in, a0), pt(r_out, a0)
            corners = [pi, po,
                       (po[0] + le_start * tx, po[1] + le_start * ty),
                       (pi[0] + le_start * tx, pi[1] + le_start * ty)]
            Polygon(*corners, align=None, mode=Mode.ADD)
    return sk.sketch


def build_rolled_plate(params: RolledParams):
    """RolledParams から円弧曲げ板金ソリッド (build123d Part) を生成する。"""
    from build123d import BuildPart, BuildSketch, Plane, add, extrude

    if params.thickness <= 0:
        raise ValueError("thickness は正である必要があります")
    r_in, r_out = params.radii()
    if r_in <= 0 or r_out <= r_in:
        raise ValueError(f"不正な半径: r_in={r_in}, r_out={r_out}")
    if not (0 < params.arc_angle_deg < 360):
        raise ValueError(f"arc_angle_deg は 0<..<360 (完全な円筒360°は revolved で対応): "
                         f"{params.arc_angle_deg}")
    if params.width <= 0:
        raise ValueError("width は正である必要があります")
    if any(L < 0 for L in params.straight_ends):
        raise ValueError(f"straight_ends に負値は指定できません: {params.straight_ends}")

    face = _sector_face_wire(r_in, r_out, params.start_angle_deg,
                             params.arc_angle_deg, params.straight_ends)
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            add(face)
        extrude(amount=params.width)
    return bp.part
