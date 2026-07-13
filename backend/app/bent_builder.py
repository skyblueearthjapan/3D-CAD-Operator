# -*- coding: utf-8 -*-
"""直線曲げ板金 (bent_plate) ビルダー。

図面の側面図に「平行ポリライン(板厚間隔)+曲げRのARC」で描かれる L字・コの字・
ハット断面の曲げ板金を、断面の中立線パスに沿って矩形(板厚×幅)をスイープして生成する。

設計方針:
  - profile_path : 板の中立線(板厚中心)の折れ線頂点 [[x,y],...] (断面=曲げ平面内)
  - bend_radii   : 各中間頂点の内曲げR。中立線でのフィレット半径 = 内R + 板厚/2
  - thickness    : 板厚 t
  - width        : 押し出し(奥行き)方向長さ。生成ソリッドは Z 中心 (-width/2..+width/2)
  - holes        : 各面(セグメント)ローカル座標の穴 (§build_bent_plate 参照)

理論体積 = (中立線の全長) × t × width  (穴があればその分を減算)。
展開長 (developed length) = 中立線の全長 と一致する。

build123d 0.11.x で検証。外部API不要・完全オフライン。
"""
from __future__ import annotations

import math
from typing import Optional

# 呼び出し側と共有する下穴表 (M呼び→下穴径, JIS並目)。ai_interpreter.TAP_DRILL と同値。
TAP_DRILL = {"M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8,
             "M10": 8.5, "M12": 10.2, "M14": 12.0, "M16": 14.0,
             "M18": 15.5, "M20": 17.5, "M24": 21.0,
             "PT1/8": 8.4, "PT1/4": 11.0, "PT3/8": 14.5, "PT1/2": 18.0}


class BentPlateError(Exception):
    """bent_plate のパラメータ/ビルド不整合。"""


def _hole_drill_diameter(h: dict) -> float:
    d = h.get("diameter")
    if d:
        return float(d)
    thread = h.get("thread")
    if thread:
        key = str(thread).upper().replace("Ｍ", "M").split("X")[0].split("×")[0].strip()
        if key in TAP_DRILL:
            return TAP_DRILL[key]
    raise BentPlateError(f"穴径を決定できません: {h}")


def _seg_len(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def developed_length(profile_path, bend_radii, thickness: float) -> float:
    """中立線の全長 (= 展開長)。90°以外の曲げ角にも対応。

    直線区間はフィレットで両端が短縮され、代わりに円弧長が加わる。
    フィレット半径 rc = 内R + t/2。中間頂点の折れ角 θ に対し、
    各直線は rc*tan(θ/2) 短縮、円弧長は rc*θ。
    """
    pts = [tuple(map(float, p)) for p in profile_path]
    n = len(pts)
    if n < 2:
        raise BentPlateError("profile_path は2点以上必要です")
    # 各直線区間の素の長さ
    seg = [_seg_len(pts[i], pts[i + 1]) for i in range(n - 1)]
    total = sum(seg)
    for k in range(1, n - 1):  # 中間頂点ごとに補正
        a, o, b = pts[k - 1], pts[k], pts[k + 1]
        v1 = (a[0] - o[0], a[1] - o[1])
        v2 = (b[0] - o[0], b[1] - o[1])
        m1 = math.hypot(*v1); m2 = math.hypot(*v2)
        if m1 < 1e-9 or m2 < 1e-9:
            continue
        cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)))
        interior = math.acos(cosang)          # 頂点の内角
        bend_angle = math.pi - interior       # 曲げ角 (直線からの振れ)
        rc = _bend_center_radius(bend_radii, k - 1, thickness)
        total -= 2 * rc * math.tan(bend_angle / 2)   # 両直線の短縮
        total += rc * bend_angle                     # 円弧
    return total


def _bend_center_radius(bend_radii, idx: int, thickness: float) -> float:
    """中間頂点 idx (0始まり) の中立線フィレット半径 = 内R + t/2。"""
    inner = thickness  # 既定の内曲げR = 板厚
    if bend_radii is not None:
        if isinstance(bend_radii, (int, float)):
            inner = float(bend_radii)
        elif idx < len(bend_radii) and bend_radii[idx] is not None:
            inner = float(bend_radii[idx])
    return inner + thickness / 2.0


def build_bent_plate(params: dict):
    """曲げ板金ソリッドを生成して build123d Part を返す。

    params:
      thickness (float, 必須)      : 板厚 t
      width     (float, 必須)      : 押し出し(奥行き)方向長さ。ソリッドは Z=±width/2
      profile_path (list[[x,y]], 必須, 2点以上) : 中立線(板厚中心)の折れ線頂点。曲げ平面=XY
      bend_radii (list[float]|float, 任意) : 各中間頂点の内曲げR。省略時=板厚。
                                     要素数は (頂点数-2)。スカラーで一括指定も可
      holes (list[dict], 任意)     : 各要素:
          segment (int)   : 穴が開く直線区間 (0始まり, profile_path[i]→[i+1])
          u (float)       : 区間始点からの距離 (中立線に沿う)
          v (float, 任意) : 幅方向位置 (0..width, 手前端=0)。省略時=中央
          diameter (float): キリ穴径。または
          thread (str)    : ねじ呼び 'M6' 等 (下穴径でモデル化)
          through (bool)  : 貫通 (既定True)
          depth (float)   : 止まり穴深さ (through=false時, 外面から)

    戻り値: build123d Part
    """
    from build123d import (BuildPart, BuildSketch, BuildLine, Polyline, Plane,
                           Rectangle, Cylinder, Locations, sweep, fillet, Mode,
                           Vector)

    t = params.get("thickness")
    width = params.get("width")
    path_pts = params.get("profile_path")
    if not t or t <= 0:
        raise BentPlateError("thickness (>0) が必要です")
    if not width or width <= 0:
        raise BentPlateError("width (>0) が必要です")
    if not path_pts or len(path_pts) < 2:
        raise BentPlateError("profile_path は2点以上必要です")
    pts = [(float(p[0]), float(p[1])) for p in path_pts]
    # 隣接重複点を除去
    dedup = [pts[0]]
    for p in pts[1:]:
        if _seg_len(dedup[-1], p) > 1e-6:
            dedup.append(p)
    pts = dedup
    if len(pts) < 2:
        raise BentPlateError("有効な profile_path 頂点が2点未満です")

    bend_radii = params.get("bend_radii")
    end_keys = {(round(pts[0][0], 4), round(pts[0][1], 4)),
                (round(pts[-1][0], 4), round(pts[-1][1], 4))}

    with BuildPart() as bp:
        with BuildLine() as ln:
            Polyline(*pts)
            # 中間頂点を個別半径でフィレット (端点は除外)
            if len(pts) > 2:
                for k in range(1, len(pts) - 1):
                    vx = [v for v in ln.vertices()
                          if abs(v.X - pts[k][0]) < 1e-4 and abs(v.Y - pts[k][1]) < 1e-4]
                    if vx:
                        rc = _bend_center_radius(bend_radii, k - 1, t)
                        try:
                            fillet(vx, radius=rc)
                        except Exception as e:
                            raise BentPlateError(
                                f"曲げ半径が大きすぎて頂点{k}をフィレットできません "
                                f"(rc={rc:.2f}): {e}")
        path = ln.line
        start = path @ 0
        tangent = path % 0
        # 断面: 幅は全体Z方向, 板厚は接線に垂直な面内方向
        sec_plane = Plane(origin=start, x_dir=(0, 0, 1), z_dir=tangent)
        with BuildSketch(sec_plane):
            Rectangle(width, t)
        sweep(path=path, is_frenet=True)

        # ---- 穴あけ (各直線区間のローカル座標)
        holes = params.get("holes") or []
        for h in holes:
            _drill_hole(h, pts, t, width, Cylinder, Locations, Plane, Vector, Mode)

    return bp.part


def _drill_hole(h, pts, t, width, Cylinder, Locations, Plane, Vector, Mode):
    seg = int(h.get("segment", 0))
    if seg < 0 or seg >= len(pts) - 1:
        raise BentPlateError(f"holes.segment={seg} が範囲外 (0..{len(pts) - 2})")
    a, b = pts[seg], pts[seg + 1]
    seglen = _seg_len(a, b)
    if seglen < 1e-9:
        raise BentPlateError(f"区間{seg}の長さが0です")
    dx, dy = (b[0] - a[0]) / seglen, (b[1] - a[1]) / seglen
    u = float(h.get("u", 0.0))
    if u < -0.01 or u > seglen + 0.01:
        raise BentPlateError(
            f"holes.u={u:.1f} が区間{seg}の範囲外です (区間長={seglen:.1f})")
    pc = (a[0] + dx * u, a[1] + dy * u)          # 中立線上 (板厚中心)
    v = h.get("v")
    if v is not None and (float(v) < -0.01 or float(v) > width + 0.01):
        raise BentPlateError(f"holes.v={v} が幅の範囲外です (width={width})")
    z = (float(v) if v is not None else width / 2.0) - width / 2.0
    nrm = (-dy, dx)                               # 面内の板厚方向 (穴軸)
    d = _hole_drill_diameter(h)
    through = h.get("through", True)
    if through:
        length = t + 2.0
        center = Vector(pc[0], pc[1], z)
    else:
        depth = float(h.get("depth") or t)
        length = depth + 0.5
        # 外面 (+法線側) から内側へ。中心を外面から length/2 内側に
        outer = (pc[0] + nrm[0] * (t / 2.0), pc[1] + nrm[1] * (t / 2.0))
        center = Vector(outer[0] - nrm[0] * (length / 2.0),
                        outer[1] - nrm[1] * (length / 2.0), z)
    plane = Plane(origin=center, z_dir=(nrm[0], nrm[1], 0.0))
    with Locations(plane):
        Cylinder(d / 2.0, length, mode=Mode.SUBTRACT)
