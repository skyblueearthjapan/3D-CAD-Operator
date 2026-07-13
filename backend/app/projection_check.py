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


def _map_pos(x, y, bb_min, size, mapping, view_origin):
    """spec座標の点を、鏡像mapping適用でビュー(図面)座標へ。"""
    ux, uy = x - bb_min[0], y - bb_min[1]
    if mapping[0]:
        ux = size[0] - ux
    if mapping[1]:
        uy = size[1] - uy
    return view_origin[0] + ux, view_origin[1] + uy


def _unmap_pos(ex, ey, bb_min, size, mapping, view_origin):
    """_map_pos の逆変換 (図面座標→spec座標)。鏡像は自己逆なので同じ式。"""
    ux, uy = ex - view_origin[0], ey - view_origin[1]
    if mapping[0]:
        ux = size[0] - ux
    if mapping[1]:
        uy = size[1] - uy
    return bb_min[0] + ux, bb_min[1] + uy


def _score_holes(view_bbox, iholes, circles, bb_min, size) -> tuple[int, list, tuple]:
    """正面ビュー候補 (bbox) に対する穴の一致数 (鏡像4通りの最良)。

    iholes は (spec.holes内のindex, HoleSpec) のリスト。
    戻り値 (一致数, 不一致穴のindexリスト, 採用した鏡像mapping)。
    """
    origin = (view_bbox[0], view_bbox[1])
    best = (-1, [i for i, _ in iholes], (False, False))
    for mx in (False, True):
        for my in (False, True):
            matched, missed = 0, []
            for i, h in iholes:
                ex, ey = _map_pos(h.x, h.y, bb_min, size, (mx, my), origin)
                radii = _accept_radii(h)
                ok = any(
                    math.hypot(cx - ex, cy - ey) <= POS_TOL
                    and any(abs(cr - r) <= RAD_TOL for r in radii)
                    for cx, cy, cr in circles) if radii else True
                if ok:
                    matched += 1
                else:
                    missed.append(i)
            if matched > best[0]:
                best = (matched, missed, (mx, my))
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
    if spec.shape_class == "rolled_plate":
        # 円弧曲げのXY投影は環状セクタの包絡矩形で、図面ビューと一致する保証がない
        return {"status": "skipped_rolled_plate", "warnings": [],
                "note": "rolled_plateは投影照合非対応(verifyの理論体積照合が主チェック)"}
    if _is_broken_view(dxfdoc):
        return {"status": "skipped_broken_view", "warnings": [],
                "note": "破断図(寸法上書き)のため投影照合をスキップ"}
    loops = detect_loops(dxfdoc.segments)
    if not loops:
        return {"status": "skipped_no_loops", "warnings": []}

    bb = solid.bounding_box()
    W, H, D = bb.size.X, bb.size.Y, bb.size.Z
    bb_min = (bb.min.X, bb.min.Y)
    # bent_plate の穴は segment/u/v ローカル座標で x/y は無意味 → 穴照合の対象外
    zholes = ([] if spec.shape_class == "bent_plate"
              else [(i, h) for i, h in enumerate(spec.holes) if h.axis == "z"])
    circles = _circle_loops(loops)

    # --- 正面ビュー候補 (XY投影と同サイズ)。閉ループ優先、なければ連結成分bbox。
    #     複数候補は穴一致数が最大のものを採用
    front = [l.bbox for l in loops if _bbox_size_match(l.bbox, W, H)]
    components = _component_bboxes(dxfdoc.segments)
    if not front:
        front = [b for b in components if _bbox_size_match(b, W, H)]
    if front:
        scored = [(_score_holes(b, zholes, circles, bb_min, (W, H)), b) for b in front]
        (matched, missed_idx, mapping), view = max(scored, key=lambda t: t[0][0])
        result = {
            "status": "ok", "warnings": [],
            "view_bbox": [round(v, 4) for v in view],
            "holes_matched": matched, "holes_total": len(zholes),
            # 以下は自動補正 (snap_fix) 用の内部情報
            "_bb_min": [bb.min.X, bb.min.Y], "_size": [W, H],
            "_mapping": list(mapping), "_missed_idx": missed_idx,
        }
        if missed_idx:
            det = "; ".join(
                f"({spec.holes[i].x:.0f},{spec.holes[i].y:.0f})"
                f"φ{spec.holes[i].diameter or spec.holes[i].thread}" for i in missed_idx[:5])
            result["status"] = "holes_missing"
            result["warnings"] = [
                f"投影照合: 穴{len(missed_idx)}/{len(zholes)}件が図面の円と一致しません"
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


# ------------------------------------------------------------------ 自動補正

def snap_fix(spec, dxfdoc, projection) -> tuple[list, list[str]] | None:
    """holes_missing の照合結果から、不一致穴を図面の実際の円位置へ吸着補正する。

    安全側の設計:
      - 位置のみ補正する (径の変更・穴の削除はしない)
      - 一致済みの穴が使っている円は候補から除外し、1つの円には1穴のみ割当
      - 不一致穴の全件が径の合う空き円に解決できた場合のみ補正を返す (部分補正はしない)
    戻り値: (補正後の holes リスト, 補正メモ) / 解決できなければ None
    """
    if not projection or projection.get("status") != "holes_missing":
        return None
    if spec.shape_class == "bent_plate":
        return None  # bent_plateの穴はローカル座標のため吸着補正の対象外
    view = projection["view_bbox"]
    mapping = tuple(projection["_mapping"])
    bb_min = tuple(projection["_bb_min"])
    size = tuple(projection["_size"])
    missed = projection["_missed_idx"]
    origin = (view[0], view[1])

    circles = _circle_loops(detect_loops(dxfdoc.segments))
    # 同心円 (皿/ザグリの二重円等) を1つの「図面上の穴」候補点にまとめる
    centers: list[list] = []  # [cx, cy, {radii}]
    for cx, cy, r in circles:
        for c in centers:
            if math.hypot(c[0] - cx, c[1] - cy) <= POS_TOL:
                c[2].add(r)
                break
        else:
            centers.append([cx, cy, {r}])
    # ビューの内側にあるものだけ
    centers = [c for c in centers
               if view[0] - 2 <= c[0] <= view[2] + 2 and view[1] - 2 <= c[1] <= view[3] + 2]

    # 一致済み穴が占有している円中心を除外
    taken: set[int] = set()
    for i, h in enumerate(spec.holes):
        if h.axis != "z" or i in missed:
            continue
        ex, ey = _map_pos(h.x, h.y, bb_min, size, mapping, origin)
        for k, c in enumerate(centers):
            if k not in taken and math.hypot(c[0] - ex, c[1] - ey) <= POS_TOL:
                taken.add(k)
                break

    new_holes = [h.model_copy() for h in spec.holes]
    notes: list[str] = []
    for i in missed:
        h = spec.holes[i]
        radii = _accept_radii(h)
        ex, ey = _map_pos(h.x, h.y, bb_min, size, mapping, origin)
        cands = [(k, c) for k, c in enumerate(centers)
                 if k not in taken
                 and any(abs(cr - r) <= RAD_TOL for cr in c[2] for r in radii)]
        if not cands:
            return None  # 径の合う空き円がない → 吸着では直せない (警告のまま人に届ける)
        k, c = min(cands, key=lambda t: math.hypot(t[1][0] - ex, t[1][1] - ey))
        taken.add(k)
        sx, sy = _unmap_pos(c[0], c[1], bb_min, size, mapping, origin)
        label = h.thread or (f"φ{h.diameter:g}" if h.diameter else "穴")
        notes.append(f"{label} ({h.x:g},{h.y:g})→({sx:.2f},{sy:.2f})")
        new_holes[i].x = round(sx, 3)
        new_holes[i].y = round(sy, 3)
    return new_holes, notes
