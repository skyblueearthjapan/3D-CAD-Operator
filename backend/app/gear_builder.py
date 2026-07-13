# -*- coding: utf-8 -*-
"""外歯平歯車 (spur gear) の 3D ソリッド生成 (Phase 1 / shape_class='gear')。

DXF→STEP 変換の形状クラス "gear" 用ビルダー。標準インボリュート平歯車の外形を
理論歯形で生成し、内径 (段付き可)・ハブ段・キー溝・PCD 穴を付与してソリッド化する。

歯形生成関数 gear_outline() は reference/build_three.py の実証済み実装
(旋回ギア m5-27T で正解 STEP と体積一致) を取り込み・整理したもの。

対応: 外歯車のみ。内歯車 (リング内側の歯) は本ビルダー非対応
      (ピッチ円の内側に歯を切るため歯形式・干渉判定が別物になり、
       ShapeSpec 側で unsupported のままとする)。

build123d の罠 (引き継ぎ書 §3): 別関数内の入れ子 BuildPart(mode=SUBTRACT) は
親に効かないため、減算はすべて Cylinder(..., mode=Mode.SUBTRACT) 等の
オブジェクト mode 引数で同一 BuildPart コンテキスト内から行う。

使い方:
    from app.gear_builder import build_gear
    part = build_gear({
        "module": 5, "teeth": 27, "pressure_angle_deg": 20,
        "tip_diameter": 145, "root_diameter": 122.5, "face_width": 57,
        "bore_diameter": 65,
        "hub_stack": [{"diameter": 96, "height": 24, "position": "above"}],
        "keyway": {"width": 18, "depth": 2.2},
    })
"""
from __future__ import annotations

import math
from typing import Any, Optional


class GearBuildError(Exception):
    """歯車ビルド時のパラメータ不備・幾何破綻。"""


# ------------------------------------------------------------------ 歯形生成

def gear_outline(m: float, z: int, ra: float, rf: float,
                 alpha_deg: float = 20.0, n_flank: int = 14, n_arc: int = 4
                 ) -> list[tuple[float, float]]:
    """標準インボリュート平歯車の外形ポリゴン (全歯) を生成する。

    m=モジュール, z=歯数, ra=歯先円半径, rf=歯底円半径, alpha_deg=圧力角。
    中心を原点とし CCW に並ぶ閉多角形の頂点列を返す。
    (reference/build_three.py の実証済み実装を取り込み)
    """
    rp = m * z / 2.0                            # 基準円 (ピッチ円) 半径
    rb = rp * math.cos(math.radians(alpha_deg))  # 基礎円半径
    inv = lambda phi: math.tan(phi) - phi        # インボリュート関数
    alpha = math.radians(alpha_deg)
    half_p = math.pi / (2 * z)                   # ピッチ円での歯厚半角

    def half_angle(r: float) -> float:           # 半径 r での歯厚半角
        phi = math.acos(max(-1.0, min(1.0, rb / r)))
        return half_p + inv(alpha) - inv(phi)

    r_lo = max(rb, rf)
    rs = [r_lo + (ra - r_lo) * i / (n_flank - 1) for i in range(n_flank)]
    pitch = 2 * math.pi / z
    pts: list[tuple[float, float]] = []
    for k in range(z):
        tc = k * pitch
        ha_b = half_angle(r_lo + 1e-9)
        # 歯底円弧 (前歯の右フランクから当該歯の左フランクまで)
        a0 = tc - pitch + ha_b
        a1 = tc - ha_b
        for i in range(1, n_arc + 1):
            a = a0 + (a1 - a0) * i / (n_arc + 1)
            pts.append((rf * math.cos(a), rf * math.sin(a)))
        if rf < rb:  # 歯元の径方向部 (歯底円が基礎円より内側のとき)
            pts.append((rf * math.cos(tc - ha_b), rf * math.sin(tc - ha_b)))
        for r in rs:  # 左フランク (上り)
            a = tc - half_angle(r)
            pts.append((r * math.cos(a), r * math.sin(a)))
        ha_a = half_angle(ra)
        for i in range(1, n_arc):  # 歯先円弧
            a = tc - ha_a + 2 * ha_a * i / n_arc
            pts.append((ra * math.cos(a), ra * math.sin(a)))
        for r in reversed(rs):  # 右フランク (下り)
            a = tc + half_angle(r)
            pts.append((r * math.cos(a), r * math.sin(a)))
        if rf < rb:
            pts.append((rf * math.cos(tc + ha_b), rf * math.sin(tc + ha_b)))
    return pts


def _shoelace(pts: list[tuple[float, float]]) -> float:
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


# ------------------------------------------------------------------ パラメータ

def _get(params: Any, key: str, default=None):
    if isinstance(params, dict):
        return params.get(key, default)
    return getattr(params, key, default)


def _num(v, name: str) -> float:
    if v is None:
        raise GearBuildError(f"{name} が指定されていません")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise GearBuildError(f"{name} が数値ではありません: {v!r}")


def gear_geometry(params: Any) -> dict:
    """params から歯車の主要諸元を解決して返す (ビルド前の検算・verify 用)。"""
    module = _num(_get(params, "module"), "module")
    teeth = int(_num(_get(params, "teeth"), "teeth"))
    alpha = float(_get(params, "pressure_angle_deg", 20.0) or 20.0)
    if module <= 0 or teeth < 3:
        raise GearBuildError(f"module/teeth が不正です (module={module}, teeth={teeth})")
    pitch_d = module * teeth
    tip_d = _get(params, "tip_diameter")
    tip_d = float(tip_d) if tip_d else module * (teeth + 2)          # 標準歯先円
    root_d = _get(params, "root_diameter")
    root_d = float(root_d) if root_d else module * (teeth - 2.5)     # 標準歯底円
    return {
        "module": module, "teeth": teeth, "pressure_angle_deg": alpha,
        "pitch_diameter": pitch_d, "tip_diameter": tip_d, "root_diameter": root_d,
        "tip_diameter_theoretical": module * (teeth + 2),
    }


# ------------------------------------------------------------------ ビルド

def build_gear(params: Any):
    """歯車 ShapeSpec 相当のパラメータから build123d の Part を生成して返す。

    params (dict 推奨。属性アクセス可能なオブジェクトも可):
      module (必須)              : モジュール
      teeth (必須)               : 歯数
      pressure_angle_deg         : 圧力角 [deg] (既定 20)
      face_width (必須)          : 歯幅 (歯部の押し出し高さ)
      tip_diameter               : 歯先円径 (省略時 module*(teeth+2))
      root_diameter              : 歯底円径 (省略時 module*(teeth-2.5))
      hub_stack                  : 歯部の上/下に付くハブ段のリスト
                                   [{"diameter":d, "height":h, "position":"above"|"below"}]
                                   (順に積む。above=歯部上面から上へ / below=歯部下面から下へ)
      bore_diameter              : 中央貫通穴径 (単純な1段穴)
      bore_stack                 : 段付き内径 [{"diameter":d,"height":h}] (上面から下へ順)
                                   ※ bore_diameter と併用時は bore_stack を優先
      keyway                     : {"width":w, "depth":t} 内径のキー溝
                                   (w=溝幅, t=内径面から半径方向の深さ)。省略可
      keyway_angle_deg           : キー溝の向き [deg] (既定 0 = +X 方向)
      holes                      : PCD 穴等のリスト (HoleSpec 相当の dict)
                                   {"x","y","diameter","thread","cbore_diameter",
                                    "cbore_depth","csk_diameter","depth","through",
                                    "from_face"}

    座標系: 歯車軸を Z 軸、歯部の底面を z=0 とする。
    """
    from build123d import (Align, Box, BuildPart, BuildSketch, CounterBoreHole,
                           CounterSinkHole, Cylinder, Hole, Location, Locations,
                           Mode, Plane, Polygon, Pos, Rot)

    CCC = (Align.CENTER, Align.CENTER, Align.MIN)

    geo = gear_geometry(params)
    face_width = _num(_get(params, "face_width"), "face_width")
    if face_width <= 0:
        raise GearBuildError(f"face_width が不正です: {face_width}")
    ra = geo["tip_diameter"] / 2.0
    rf = geo["root_diameter"] / 2.0
    if rf >= ra:
        raise GearBuildError(f"歯底円>=歯先円で歯が成立しません (ra={ra}, rf={rf})")

    pts = gear_outline(geo["module"], geo["teeth"], ra, rf, geo["pressure_angle_deg"])

    # ハブ段を上下に振り分け
    hub_stack = list(_get(params, "hub_stack", []) or [])
    above = [h for h in hub_stack if (str(_get(h, "position", "above")).lower() != "below")]
    below = [h for h in hub_stack if (str(_get(h, "position", "above")).lower() == "below")]

    z_bottom = -sum(_num(_get(h, "height"), "hub.height") for h in below)
    z_top = face_width + sum(_num(_get(h, "height"), "hub.height") for h in above)
    total_h = z_top - z_bottom

    with BuildPart() as p:
        # --- 歯部本体 (歯先外形をそのまま押し出し)
        with BuildSketch(Plane.XY):
            Polygon(*pts, align=None)
        from build123d import extrude
        extrude(amount=face_width)

        # --- ハブ段 (above: 歯部上面から上へ / below: 歯部下面から下へ)
        z = face_width
        for h in above:
            hh = _num(_get(h, "height"), "hub.height")
            hd = _num(_get(h, "diameter"), "hub.diameter")
            with Locations(Location((0, 0, z))):
                Cylinder(hd / 2, hh, align=CCC)
            z += hh
        z = 0.0
        for h in below:
            hh = _num(_get(h, "height"), "hub.height")
            hd = _num(_get(h, "diameter"), "hub.diameter")
            with Locations(Location((0, 0, z - hh))):
                Cylinder(hd / 2, hh, align=CCC)
            z -= hh

        # --- 内径 (段付き or 単純貫通)
        bore_stack = _get(params, "bore_stack")
        bore_r_at_top = None  # キー溝の基準半径 (最上段の内径)
        if bore_stack:
            z_hi = z_top
            for s in bore_stack:
                sh = _num(_get(s, "height"), "bore.height")
                sd = _num(_get(s, "diameter"), "bore.diameter")
                z_lo = z_hi - sh
                ext_top = 0.5 if abs(z_hi - z_top) < 1e-9 else 0.0
                ext_bot = 0.5 if z_lo <= z_bottom + 1e-9 else 0.0
                with Locations(Location((0, 0, z_lo - ext_bot))):
                    Cylinder(sd / 2, sh + ext_top + ext_bot, align=CCC, mode=Mode.SUBTRACT)
                if bore_r_at_top is None:
                    bore_r_at_top = sd / 2
                z_hi = z_lo
        else:
            bd = _get(params, "bore_diameter")
            if bd:
                br = float(bd) / 2.0
                bore_r_at_top = br
                with Locations(Location((0, 0, z_bottom - 0.5))):
                    Cylinder(br, total_h + 1.0, align=CCC, mode=Mode.SUBTRACT)

        # --- キー溝 (内径面から半径方向 depth ぶん切り欠く)
        keyway = _get(params, "keyway")
        if keyway:
            kw = _num(_get(keyway, "width"), "keyway.width")
            kd = _num(_get(keyway, "depth"), "keyway.depth")
            if bore_r_at_top is None:
                raise GearBuildError("キー溝には内径 (bore_diameter か bore_stack) が必要です")
            outer = bore_r_at_top + kd            # 溝の外縁 (中心からの距離)
            ang = float(_get(params, "keyway_angle_deg", 0.0) or 0.0)
            # 中心を跨いで +X 方向へ延びる矩形 (中心側は内径円と重なるので実質は溝のみ残る)
            with Locations(Pos(0, 0, z_bottom - 0.5) * Rot(0, 0, ang)):
                Box(outer, kw, total_h + 1.0,
                    align=(Align.MIN, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

        # --- PCD 穴等
        _apply_holes(params, z_top, z_bottom,
                     Pos=Pos, Rot=Rot, Locations=Locations,
                     Hole=Hole, CounterBoreHole=CounterBoreHole,
                     CounterSinkHole=CounterSinkHole, Mode=Mode)

    return p.part


# タップ→下穴径 (ai_interpreter.TAP_DRILL と同一表。単独動作のため複製)
_TAP_DRILL = {"M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8,
              "M10": 8.5, "M12": 10.2, "M14": 12.0, "M16": 14.0,
              "M18": 15.5, "M20": 17.5, "M24": 21.0,
              "PT1/8": 8.4, "PT1/4": 11.0, "PT3/8": 14.5, "PT1/2": 18.0}


def _hole_drill_diameter(h: Any) -> float:
    d = _get(h, "diameter")
    if d:
        return float(d)
    thread = _get(h, "thread")
    if thread:
        key = str(thread).upper().replace("Ｍ", "M").split("X")[0].split("×")[0].strip()
        if key in _TAP_DRILL:
            return _TAP_DRILL[key]
    raise GearBuildError(f"穴径を決定できません: {h}")


def _apply_holes(params, z_top, z_bottom, *, Pos, Rot, Locations,
                 Hole, CounterBoreHole, CounterSinkHole, Mode):
    holes = _get(params, "holes", []) or []
    for h in holes:
        d = _hole_drill_diameter(h)
        x = float(_get(h, "x", 0.0) or 0.0)
        y = float(_get(h, "y", 0.0) or 0.0)
        through = _get(h, "through", True)
        depth = _get(h, "depth")
        from_face = str(_get(h, "from_face", "top") or "top")
        loc = (Pos(x, y, z_top) if from_face == "top"
               else Pos(x, y, z_bottom) * Rot(180, 0, 0))
        with Locations(loc):
            csk = _get(h, "csk_diameter")
            cbd = _get(h, "cbore_diameter")
            if csk:
                CounterSinkHole(radius=d / 2, counter_sink_radius=float(csk) / 2,
                                counter_sink_angle=90, mode=Mode.SUBTRACT)
            elif cbd:
                CounterBoreHole(radius=d / 2, counter_bore_radius=float(cbd) / 2,
                                counter_bore_depth=float(_get(h, "cbore_depth", 0.0) or 0.0),
                                mode=Mode.SUBTRACT)
            else:
                Hole(radius=d / 2,
                     depth=None if through else (float(depth) if depth else None))
