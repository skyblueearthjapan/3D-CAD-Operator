# -*- coding: utf-8 -*-
"""Phase 0: 2D投影照合 — 生成ソリッドの投影と図面輪郭の機械照合 (LLM不要の最終防壁)。

AI解釈で生成したソリッドが図面と食い違っていないかを幾何のみで確認する:
  1. ソリッドのXY投影(真上から見た外形サイズ)と一致する閉輪郭が図面内に存在するか
     → 一致する輪郭 = 部品の正面ビューとみなす
  2. その正面ビュー内で、仕様の各穴(axis='z')が図面の円と位置・径で一致するか

誤警報を避ける設計:
  - 破断図(DIMENSIONの寸法テキスト上書き)を検出したら照合自体をスキップ
  - 外形輪郭は閉ループだけでなくセグメント連結成分のbboxでも探す
    (内部の線でビューが小さい面に分割される図面では全体輪郭が閉ループとして出ないため)
  - 正面ビューが見つからず側面ビュー相当だけ見つかった場合、穴照合はスキップ
  - ビュー原点の取り方の差を吸収するため鏡像4通りの対応付けを試し、最良を採用
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from .contours import Loop, detect_loops

BBOX_TOL = 1.0      # 外形bbox一致の許容 [mm] (サイズ比例分は _tol で加算)
POS_TOL = 1.5       # 穴中心位置の許容 [mm]
RAD_TOL = 0.6       # 穴半径の許容 [mm]


def _tol(size: float) -> float:
    return max(BBOX_TOL, size * 0.005)


def _is_broken_view(dxfdoc) -> bool:
    """破断図の兆候: 純数値の寸法テキスト上書きが実測値と3%以上乖離。"""
    try:
        dims = dxfdoc.msp.query("DIMENSION")
    except Exception:
        return False
    for e in dims:
        txt = (e.dxf.text or "").strip()
        if not txt or txt == "<>":
            continue
        txt = re.sub(r"\\A\d;", "", txt).strip()
        import unicodedata
        txt = unicodedata.normalize("NFKC", txt)
        # 純数値 + 短い英字サフィックス ('1830' / '1350st' 等) のみ対象
        mnum = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*[A-Za-z]{0,3}", txt)
        if not mnum:
            continue
        try:
            m = float(e.get_measurement())
        except Exception:
            continue
        v = float(mnum.group(1))
        if m > 1e-6 and abs(v - m) / max(v, m) > 0.03:
            return True
    return False


def _circle_loops(loops: list[Loop]) -> list[tuple[float, float, float]]:
    """円1本で閉じたループ → (cx, cy, r) のリスト。"""
    out = []
    for l in loops:
        if len(l.steps) == 1 and l.steps[0][0].kind == "circle":
            s = l.steps[0][0]
            out.append((s.center[0], s.center[1], s.radius))
    return out


def _accept_radii(hole) -> set[float]:
    """図面上でこの穴として描かれ得る円の半径の候補 (キリ/皿/ザグリ/ねじ呼び/下穴)。"""
    from .ai_interpreter import TAP_DRILL
    rs: set[float] = set()
    if hole.diameter:
        rs.add(hole.diameter / 2)
    if hole.csk_diameter:
        rs.add(hole.csk_diameter / 2)
    if hole.cbore_diameter:
        rs.add(hole.cbore_diameter / 2)
    if hole.thread:
        key = hole.thread.upper().replace("Ｍ", "M").split("X")[0].split("×")[0].strip()
        if key in TAP_DRILL:
            rs.add(TAP_DRILL[key] / 2)
        m = re.match(r"M([0-9]+(?:\.[0-9]+)?)", key)
        if m:
            rs.add(float(m.group(1)) / 2)  # 呼び径で描かれる場合
    return rs


def _component_bboxes(segments, min_segs: int = 4) -> list[tuple[float, float, float, float]]:
    """端点が繋がったセグメント群 (=1つのビュー相当) ごとのbbox。

    閉ループ検出は「最小面」を返すため、内部の線でビューが分割される図面では
    全体輪郭が得られない。連結成分のbboxはその場合でもビュー外形サイズを与える。
    """
    parent: dict = {}

    def find(a):
        while parent.get(a, a) != a:
            parent[a] = parent.get(parent[a], parent[a])
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    def q(p):
        return (round(p[0] / 0.02), round(p[1] / 0.02))

    for s in segments:
        if s.kind == "circle":
            continue  # 円は独立輪郭 (閉ループ側で扱う)
        union(q(s.p0), q(s.p1))
    boxes: dict = defaultdict(lambda: [1e18, 1e18, -1e18, -1e18, 0])
    for s in segments:
        if s.kind == "circle":
            continue
        r = find(q(s.p0))
        b = boxes[r]
        for x, y in s.flatten():
            b[0] = min(b[0], x); b[1] = min(b[1], y)
            b[2] = max(b[2], x); b[3] = max(b[3], y)
        b[4] += 1
    return [tuple(b[:4]) for b in boxes.values() if b[4] >= min_segs]


def _bbox_size_match(bbox, w: float, h: float) -> bool:
    lw = bbox[2] - bbox[0]
    lh = bbox[3] - bbox[1]
    tw, th = _tol(w), _tol(h)
    return ((abs(lw - w) <= tw and abs(lh - h) <= th) or
            (abs(lw - h) <= th and abs(lh - w) <= tw))


def _score_holes(view_bbox, holes: list, circles, bb_min, size) -> tuple[int, list]:
    """正面ビュー候補 (bbox) に対する穴の一致数 (鏡像4通りの最良)。戻り値 (一致数, 不一致穴)。"""
    W, H = size
    lx, ly = view_bbox[0], view_bbox[1]
    best = (-1, holes)
    for mx in (False, True):
        for my in (False, True):
            matched, missed = 0, []
            for h in holes:
                ux, uy = h.x - bb_min[0], h.y - bb_min[1]
                if mx:
                    ux = W - ux
                if my:
                    uy = H - uy
                ex, ey = lx + ux, ly + uy
                radii = _accept_radii(h)
                ok = any(
                    math.hypot(cx - ex, cy - ey) <= POS_TOL
                    and any(abs(cr - r) <= RAD_TOL for r in radii)
                    for cx, cy, cr in circles) if radii else True
                if ok:
                    matched += 1
                else:
                    missed.append(h)
            if matched > best[0]:
                best = (matched, missed)
    return best


def check_projection(solid, spec, dxfdoc) -> dict:
    """生成ソリッドと図面輪郭の照合。

    戻り値: {"status": ..., "warnings": [...], ...}
      ok               : 正面ビュー一致 + 全穴一致
      ok_outline_only  : 外形サイズ一致のビューはあるが穴照合は不可(側面のみ等)
      holes_missing    : 正面ビューはあるが一致しない穴がある → warning
      no_view          : 投影と一致する外形輪郭が図面にない → warning
      skipped_*        : 照合不可 (破断図・輪郭検出ゼロ) → warningなし
    """
    if _is_broken_view(dxfdoc):
        return {"status": "skipped_broken_view", "warnings": [],
                "note": "破断図(寸法上書き)のため投影照合をスキップ"}
    loops = detect_loops(dxfdoc.segments)
    if not loops:
        return {"status": "skipped_no_loops", "warnings": []}

    bb = solid.bounding_box()
    W, H, D = bb.size.X, bb.size.Y, bb.size.Z
    bb_min = (bb.min.X, bb.min.Y)
    zholes = [h for h in spec.holes if h.axis == "z"]
    circles = _circle_loops(loops)

    # --- 正面ビュー候補 (XY投影と同サイズ)。閉ループ優先、なければ連結成分bbox。
    #     複数候補は穴一致数が最大のものを採用
    front = [l.bbox for l in loops if _bbox_size_match(l.bbox, W, H)]
    components = _component_bboxes(dxfdoc.segments)
    if not front:
        front = [b for b in components if _bbox_size_match(b, W, H)]
    if front:
        scored = [(_score_holes(b, zholes, circles, bb_min, (W, H)), b) for b in front]
        (matched, missed), view = max(scored, key=lambda t: t[0][0])
        result = {
            "status": "ok", "warnings": [],
            "view_bbox": [round(v, 2) for v in view],
            "holes_matched": matched, "holes_total": len(zholes),
        }
        if missed:
            det = "; ".join(
                f"({h.x:.0f},{h.y:.0f})φ{h.diameter or h.thread}" for h in missed[:5])
            result["status"] = "holes_missing"
            result["warnings"] = [
                f"投影照合: 穴{len(missed)}/{len(zholes)}件が図面の円と一致しません"
                f" [{det}] — 座標・径の解釈ミスの可能性"]
        return result

    # --- 側面/断面ビュー相当 (板厚・全長方向を含むサイズ) なら外形のみOK扱い
    candidates = [l.bbox for l in loops] + components
    for tw, th in ((W, D), (H, D)):
        if D > 1e-6 and any(_bbox_size_match(b, tw, th) for b in candidates):
            return {"status": "ok_outline_only", "warnings": [],
                    "note": "正面ビュー未検出 (側面/断面サイズの輪郭のみ一致)。穴照合スキップ"}

    return {"status": "no_view", "warnings": [
        f"投影照合: 生成形状の投影({W:.0f}×{H:.0f})と一致する外形輪郭が"
        f"図面に見つかりません — 外形寸法の解釈ミスの可能性"]}
