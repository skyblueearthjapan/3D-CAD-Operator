# -*- coding: utf-8 -*-
"""閉輪郭の自動検出。

線分・円弧を端点で連結して平面グラフを構築し、最小閉領域 (面) を抽出する。
中心線や寸法線の食み出しはダングリングエッジ除去で自動的に消える。

アルゴリズム:
  1. 端点を許容誤差で量子化してノード化
  2. 次数 1 のノードに繋がるエッジを反復的に削除 (ダングリング除去)
  3. 各ノードで接続エッジを接線方向の角度順にソート
  4. 有向エッジを「到着方向から時計回りに次のエッジ」規則で辿り、最小面を列挙
  5. 符号付き面積が正 (CCW) の面のみ採用 → 最外周・穴とも個別の輪郭として得られる
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .dxf_parser import Segment

TOL = 0.02          # 端点一致の許容誤差 [mm]
MIN_AREA = 0.05     # これ未満の面積の輪郭は無視 [mm^2]
MAX_LOOPS = 300


@dataclass
class Loop:
    """検出された閉輪郭。steps は (segment, forward) の順列。"""
    id: int
    steps: list[tuple[Segment, bool]]
    area: float = 0.0
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    poly: list[tuple[float, float]] = field(default_factory=list)
    inside_of: list[int] = field(default_factory=list)  # このループを含むループ ID

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "area": round(self.area, 3),
            "bbox": [round(v, 3) for v in self.bbox],
            "poly": [(round(x, 3), round(y, 3)) for x, y in self.poly],
            "insideOf": self.inside_of,
            "isCircle": len(self.steps) == 1 and self.steps[0][0].kind == "circle",
        }


def _q(p: tuple[float, float]) -> tuple[int, int]:
    return (round(p[0] / TOL), round(p[1] / TOL))


def _seg_dir(seg: Segment, from_start: bool) -> tuple[float, float]:
    """端点から出て行く方向の単位ベクトル。"""
    if seg.kind == "arc":
        return seg.tangent_at(at_start=from_start, forward=from_start)
    pts = seg.points if seg.kind == "poly" else [seg.p0, seg.p1]
    if from_start:
        a, b = pts[0], pts[1]
    else:
        a, b = pts[-1], pts[-2]
    d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def detect_loops(segments: list[Segment], layers: set[str] | None = None) -> list[Loop]:
    segs = [s for s in segments if layers is None or s.layer in layers]

    loops: list[Loop] = []
    loop_id = 0

    # --- 円は単独で閉ループ
    for s in segs:
        if s.kind == "circle":
            loop_id += 1
            loops.append(_finish(Loop(loop_id, [(s, True)])))

    open_segs = [s for s in segs if s.kind != "circle" and _q(s.p0) != _q(s.p1)]
    closed_polys = [s for s in segs if s.kind == "poly" and _q(s.p0) == _q(s.p1)]
    for s in closed_polys:  # 閉じたスプライン等
        loop_id += 1
        loops.append(_finish(Loop(loop_id, [(s, True)])))

    # --- グラフ構築
    adj: dict[tuple[int, int], list[tuple[Segment, bool]]] = defaultdict(list)
    for s in open_segs:
        adj[_q(s.p0)].append((s, True))    # p0 から出発 (forward)
        adj[_q(s.p1)].append((s, False))   # p1 から出発 (backward)

    # --- ダングリングエッジの反復除去
    removed: set[int] = set()
    changed = True
    while changed:
        changed = False
        for node, lst in adj.items():
            alive = [x for x in lst if x[0].id not in removed]
            if len(alive) == 1:
                removed.add(alive[0][0].id)
                changed = True

    # --- 角度順ソート
    order: dict[tuple[int, int], list[tuple[Segment, bool]]] = {}
    for node, lst in adj.items():
        alive = [x for x in lst if x[0].id not in removed]
        alive.sort(key=lambda x: math.atan2(*reversed(_seg_dir(x[0], x[1]))))
        order[node] = alive

    # --- 面トラバース
    used: set[tuple[int, bool]] = set()  # 有向エッジ (seg_id, forward)

    def next_step(seg: Segment, forward: bool) -> tuple[Segment, bool] | None:
        end = _q(seg.p1 if forward else seg.p0)
        lst = order.get(end, [])
        if not lst:
            return None
        # 到着方向の逆向きベクトルの角度
        dx, dy = _seg_dir(seg, from_start=not forward)  # 終端から戻る方向
        back = math.atan2(dy, dx)
        # back よりわずかに小さい角度のエッジ = 時計回りで次 (最小面規則)
        best, best_delta = None, None
        for cand in lst:
            if cand[0].id == seg.id and cand[1] != forward and len(lst) > 1:
                continue  # 折り返しは他に選択肢があれば避ける
            a = math.atan2(*reversed(_seg_dir(cand[0], cand[1])))
            delta = (back - a) % (2 * math.pi)
            if delta < 1e-12:
                delta = 2 * math.pi
            if best_delta is None or delta < best_delta:
                best, best_delta = cand, delta
        return best

    for node in list(order.keys()):
        for start in order[node]:
            if (start[0].id, start[1]) in used:
                continue
            path: list[tuple[Segment, bool]] = []
            cur = start
            ok = False
            for _ in range(10000):
                if (cur[0].id, cur[1]) in used:
                    break
                used.add((cur[0].id, cur[1]))
                path.append(cur)
                nxt = next_step(cur[0], cur[1])
                if nxt is None:
                    break
                if nxt[0].id == start[0].id and nxt[1] == start[1]:
                    ok = True
                    break
                cur = nxt
            if ok and path:
                loop_id += 1
                lp = _finish(Loop(loop_id, path))
                if lp.area > MIN_AREA:  # CCW (正) のみ = 内部面
                    loops.append(lp)

    # --- 面積順・上限
    loops.sort(key=lambda l: -abs(l.area))
    loops = loops[:MAX_LOOPS]

    # --- 包含関係
    for a in loops:
        for b in loops:
            if a.id == b.id:
                continue
            if _contains(a, b):
                b.inside_of.append(a.id)

    return loops


def _finish(loop: Loop) -> Loop:
    pts: list[tuple[float, float]] = []
    for seg, fwd in loop.steps:
        f = seg.flatten()
        if not fwd:
            f = f[::-1]
        if pts:
            f = f[1:]
        pts.extend(f)
    loop.poly = pts
    # shoelace
    area = 0.0
    for i in range(len(pts) - 1):
        area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    if len(pts) > 2:
        area += pts[-1][0] * pts[0][1] - pts[0][0] * pts[-1][1]
    loop.area = area / 2.0
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    loop.bbox = (min(xs), min(ys), max(xs), max(ys))
    return loop


def _point_in_poly(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            t = (y - yi) / (yj - yi)
            if x < xi + t * (xj - xi):
                inside = not inside
        j = i
    return inside


def _contains(outer: Loop, inner: Loop) -> bool:
    ob, ib = outer.bbox, inner.bbox
    if not (ob[0] <= ib[0] and ob[1] <= ib[1] and ob[2] >= ib[2] and ob[3] >= ib[3]):
        return False
    if abs(outer.area) <= abs(inner.area):
        return False
    # 代表点 (数点) の多数決
    hits = sum(1 for p in inner.poly[:: max(1, len(inner.poly) // 5)]
               if _point_in_poly(p, outer.poly))
    total = len(inner.poly[:: max(1, len(inner.poly) // 5)])
    return hits > total / 2
