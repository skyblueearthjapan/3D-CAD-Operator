# -*- coding: utf-8 -*-
"""revolved(旋盤物)ソリッドへの後付け特徴群 (Phase 4)。

build_from_spec() の revolved ブロック内、apply_holes() の直後
(BuildPart コンテキスト `p` がアクティブな状態) から呼び出す前提の関数群:

  apply_radial_holes(p, radial_holes, outer_stack)  半径方向の穴/タップ(ホーロー等)
  apply_keyway(p, keyway, bore_stack)               内径キー溝(JIS 平行キー)+ 任意で軸側キー溝

build123d の罠対策: 別関数内の入れ子 BuildPart(mode=Mode.SUBTRACT) は親に効かないため、
必ず Cylinder/Box の mode=Mode.SUBTRACT 引数で減算する(オブジェクト単位の減算)。
これらの関数はアクティブな BuildPart のコンテキストスタックに対して働くので、
呼び出しは `with BuildPart() as p:` ブロックの内側で行うこと。

外部APIには一切依存しない(オフラインで完結)。
"""
from __future__ import annotations

import math
from typing import Optional

# TAP_DRILL は既存エンジンの下穴径表を再利用(単一の情報源)。
try:  # パッケージ実行時
    from .ai_interpreter import TAP_DRILL
except Exception:  # 単体テスト等でパッケージ外から読む場合
    from ai_interpreter import TAP_DRILL  # type: ignore


# ------------------------------------------------------------------ 補助

def _attr(obj, key, default=None):
    """dict / Pydantic / 一般オブジェクトのいずれからでも値を取り出す。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        v = getattr(obj, key, default)
    return default if v is None else v


def _resolve_drill(diameter: Optional[float], thread: Optional[str]) -> float:
    """穴径を決定。diameter があればそれ、無ければ thread の下穴径 (TAP_DRILL)。"""
    if diameter:
        return float(diameter)
    if thread:
        key = (str(thread).upper().replace("Ｍ", "M").replace("×", "X")
               .split("X")[0].strip())
        if key in TAP_DRILL:
            return TAP_DRILL[key]
    raise ValueError(f"穴径を決定できません (diameter/thread いずれも不正): "
                     f"diameter={diameter} thread={thread}")


def _outer_radius_at_z(outer_stack, z: float) -> float:
    """outer_stack(底面 z=0 から上へ)で高さ z における外半径を返す。"""
    zc = 0.0
    last_r = 0.0
    for s in outer_stack:
        d = _attr(s, "diameter")
        h = _attr(s, "height")
        last_r = d / 2.0
        if zc - 1e-6 <= z <= zc + h + 1e-6:
            return d / 2.0
        zc += h
    return last_r  # z が範囲外なら最上段の半径


def _bore_radius_at_z(bore_stack, total_h: float, z: float) -> Optional[float]:
    """bore_stack(上面から下へ)で高さ z における内半径を返す。範囲外は None。

    ai_interpreter の revolved 内径ロジックと同じく、内径段は total_h から下向きに積む。
    """
    z_hi = total_h
    for s in bore_stack:
        h = _attr(s, "height")
        d = _attr(s, "diameter")
        z_lo = z_hi - h
        if z_lo - 1e-6 <= z <= z_hi + 1e-6:
            return d / 2.0
        z_hi = z_lo
    return None


# ------------------------------------------------------------------ 半径方向穴/タップ

def apply_radial_holes(part_ctx, radial_holes, outer_stack) -> list[str]:
    """半径方向(軸に直交)の穴/タップを外周面から掘る。

    radial_holes: 各要素 = {z(軸方向高さ), angle_deg(0=+X, CCW), diameter または thread,
                            depth(省略時=軸中心まで貫通)}。
    outer_stack: revolved の外形段(外半径をその z 高さで参照するため)。
    戻り値: 適用内容のメモ(assumptions などに載せる用途)。

    実装: 既定 Z 軸のシリンダを Rot(0,90,angle) で半径方向へ倒し、外周のわずか外側から
    内側へ depth 分だけ掘る。mode=Mode.SUBTRACT でアクティブ BuildPart から減算。
    """
    from build123d import Cylinder, Locations, Mode, Pos, Rot

    notes: list[str] = []
    if not radial_holes:
        return notes
    for h in radial_holes:
        z = float(_attr(h, "z", 0.0))
        ang = float(_attr(h, "angle_deg", 0.0))
        d = _resolve_drill(_attr(h, "diameter"), _attr(h, "thread"))
        depth = _attr(h, "depth")

        r_out = _outer_radius_at_z(outer_stack, z)
        outer_end = r_out + 0.5  # 外周をわずかに超えて確実に開口
        if depth is not None:
            r_in = max(0.0, r_out - float(depth))
        else:
            r_in = 0.0  # 既定: 軸中心まで(=貫通側)。止まり穴は depth を指定する
        length = outer_end - r_in
        r_center = (outer_end + r_in) / 2.0
        a = math.radians(ang)
        cx, cy = r_center * math.cos(a), r_center * math.sin(a)
        # 回転順序の根拠(build123d の Rot 合成は右が先に適用される):
        #   Rot(0, 90, 0)  : Z 軸のシリンダを X 軸方向へ倒す(90° around Y)
        #   Rot(0, 0, ang) : 倒したシリンダを Z 周りに ang° 回転 → 角度 ang の半径方向へ向ける
        # NG例: Rot(0, 90, ang) は Euler ZYX 順で Rz(ang) が先=Z 軸回転後に Y 倒しになり、
        #       ang の値に関わらず常に +X 方向にしか掘れない(=全穴が同一方向バグ)。
        with Locations(Pos(cx, cy, z) * Rot(0, 0, ang) * Rot(0, 90, 0)):
            Cylinder(d / 2.0, length, mode=Mode.SUBTRACT)

        thr = _attr(h, "thread")
        kind = f"タップ{thr}(下穴φ{d:g})" if thr else f"φ{d:g}"
        depth_s = "軸中心まで" if depth is None else f"深さ{float(depth):g}"
        notes.append(f"半径方向{kind} @z{z:g} 角度{ang:g}° {depth_s}")
    return notes


# ------------------------------------------------------------------ キー溝

def apply_keyway(part_ctx, keyway, bore_stack) -> list[str]:
    """内径キー溝(JIS B 1301 平行キー)を掘る。任意で軸側(外径面)キー溝も。

    keyway: {width(溝幅), depth(内径面からの溝深さ t2 相当), z_from, z_to(省略時=貫通),
             angle_deg(既定0=+X), bore_diameter(省略時 bore_stack から推定),
             side("bore"=内径既定 / "shaft"=軸外径), shaft_diameter(side="shaft"時)}。
    bore_stack: revolved の内径段(内半径を参照)。
    戻り値: 適用内容のメモ。

    実装: 矩形の Box を mode=Mode.SUBTRACT で減算。内径キー溝は中心から
    (内半径+depth) まで X+ 方向へ伸ばし、bore 内側は既に空洞なので溝側面のみが成形される。
    angle_deg で溝の周方向位置を回す。
    """
    from build123d import Align, Box, Locations, Mode, Pos, Rot

    notes: list[str] = []
    if not keyway:
        return notes

    width = float(_attr(keyway, "width"))
    depth = float(_attr(keyway, "depth"))
    ang = float(_attr(keyway, "angle_deg", 0.0))
    side = _attr(keyway, "side", "bore")

    z_from = _attr(keyway, "z_from")
    z_to = _attr(keyway, "z_to")
    z0 = (float(z_from) if z_from is not None else 0.0) - 0.5
    if z_to is not None:
        z1 = float(z_to) + 0.5
    else:
        # 貫通: SUBTRACT なので実体外の余分は無害。bore 全長を確実に覆う大きめの上端。
        bore_total = sum(float(_attr(s, "height")) for s in (bore_stack or [])) + 1000.0
        z1 = bore_total
    z_len = z1 - z0

    if side == "shaft":
        # 軸側(外径面)キー溝: 外径面から内向きに depth。shaft_diameter が必須。
        sd = _attr(keyway, "shaft_diameter")
        if sd is None:
            raise ValueError("軸側キー溝には shaft_diameter が必要です")
        r_shaft = float(sd) / 2.0
        # 外周のすぐ外(r_shaft+0.5)から内向き depth 分を削る矩形。
        # 起点 x_start(=r_shaft-depth)に Location を置き、局所+X(=angle方向)へ x_len 伸ばす。
        x_len = depth + 0.5
        x_start = r_shaft + 0.5 - x_len  # = r_shaft - depth
        a = math.radians(ang)
        with Locations(Pos(x_start * math.cos(a), x_start * math.sin(a), z0)
                       * Rot(0, 0, ang)):
            Box(x_len, width, z_len,
                align=(Align.MIN, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT)
        notes.append(f"軸側キー溝 幅{width:g}×深さ{depth:g} @軸φ{2*r_shaft:g} 角度{ang:g}°")
        return notes

    # 内径キー溝(既定)
    bore_d = _attr(keyway, "bore_diameter")
    if bore_d is not None:
        r_bore = float(bore_d) / 2.0
    else:
        z_mid = (max(z0, 0.0) + z1) / 2.0
        bore_total = sum(float(_attr(s, "height")) for s in (bore_stack or []))
        r_bore = _bore_radius_at_z(bore_stack, bore_total, z_mid)
        if r_bore is None and bore_stack:
            r_bore = max(_attr(s, "diameter") for s in bore_stack) / 2.0
        if r_bore is None:
            raise ValueError("内径キー溝には bore_stack か bore_diameter が必要です")

    x_len = r_bore + depth  # 中心から溝底(内半径+depth)まで
    with Locations(Pos(0, 0, z0) * Rot(0, 0, ang)):
        Box(x_len, width, z_len,
            align=(Align.MIN, Align.CENTER, Align.MIN),
            mode=Mode.SUBTRACT)
    notes.append(f"内径キー溝 幅{width:g}×深さ{depth:g} @内径φ{2*r_bore:g} 角度{ang:g}°")
    return notes
