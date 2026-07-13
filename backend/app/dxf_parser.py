# -*- coding: utf-8 -*-
"""DXF 読み込み・解析。

図面を 2 種類のデータに分解する:
  - display: フロントエンドの 2D ビューアに描画するためのポリライン/テキスト
  - segments: 輪郭検出・3D モデリングに使う厳密な幾何プリミティブ (line/arc/circle/poly)

寸法・引出線・文字は display のみに入れる (モデリング対象外)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

import ezdxf
from ezdxf import colors as ezcolors
from ezdxf import path as ezpath
from ezdxf.entities import DXFGraphic

FLATTEN_DIST = 0.05   # 曲線の平坦化許容誤差 [mm] (表示・面積計算用)
MAX_INSERT_DEPTH = 8

# モデリング対象となるエンティティ
GEOMETRY_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}
# 表示のみ (仮想エンティティに展開)
EXPLODE_TYPES = {"DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "ACAD_TABLE"}


@dataclass
class Segment:
    """モデリング用の幾何プリミティブ (XY 平面)。"""
    id: int
    kind: str                 # 'line' | 'arc' | 'circle' | 'poly'
    layer: str
    p0: tuple[float, float] = (0.0, 0.0)
    p1: tuple[float, float] = (0.0, 0.0)
    # arc / circle
    center: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    start_angle: float = 0.0  # degrees, CCW
    end_angle: float = 0.0
    # poly (スプライン等の近似)
    points: list[tuple[float, float]] = field(default_factory=list)

    def sweep(self) -> float:
        """円弧の掃引角 [deg] (常に正、CCW)。"""
        s = (self.end_angle - self.start_angle) % 360.0
        return s if s > 1e-9 else 360.0

    def point_at(self, deg: float) -> tuple[float, float]:
        r = math.radians(deg)
        return (self.center[0] + self.radius * math.cos(r),
                self.center[1] + self.radius * math.sin(r))

    def mid_point(self) -> tuple[float, float]:
        """円弧の中間点 (向きに依存しない)。"""
        return self.point_at(self.start_angle + self.sweep() / 2.0)

    def tangent_at(self, at_start: bool, forward: bool) -> tuple[float, float]:
        """円弧端点での進行方向接線。forward=True なら p0→p1 方向に進む場合。"""
        deg = self.start_angle if at_start else self.end_angle
        r = math.radians(deg)
        # CCW 進行の接線
        tx, ty = -math.sin(r), math.cos(r)
        if not forward:
            tx, ty = -tx, -ty
        return (tx, ty)

    def flatten(self) -> list[tuple[float, float]]:
        """p0→p1 方向の折れ線近似 (両端を含む)。"""
        if self.kind == "line":
            return [self.p0, self.p1]
        if self.kind == "poly":
            return list(self.points)
        if self.kind in ("arc", "circle"):
            sweep = self.sweep() if self.kind == "arc" else 360.0
            # 弦誤差 FLATTEN_DIST を満たす分割数
            if self.radius <= FLATTEN_DIST:
                n = 4
            else:
                step = 2.0 * math.degrees(math.acos(max(-1.0, 1.0 - FLATTEN_DIST / self.radius)))
                n = max(4, int(math.ceil(sweep / max(step, 0.5))))
            pts = [self.point_at(self.start_angle + sweep * i / n) for i in range(n + 1)]
            return pts
        return [self.p0, self.p1]


def _aci_to_hex(aci: int) -> str:
    try:
        r, g, b = ezcolors.aci2rgb(aci)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#c8c8c8"


class DxfDocument:
    """解析済み DXF。表示データとモデリング用セグメントを保持する。"""

    def __init__(self, filepath: str):
        self.doc = ezdxf.readfile(filepath)
        self.msp = self.doc.modelspace()
        self.display: list[dict] = []
        self.segments: list[Segment] = []
        self._seg_id = 0
        self._layer_colors: dict[str, str] = {}
        for layer in self.doc.layers:
            self._layer_colors[layer.dxf.name] = _aci_to_hex(abs(layer.color))
        self._walk(self.msp, depth=0, in_display_block=False)

    # ---------------------------------------------------------------- walk

    def _walk(self, entities: Iterator[DXFGraphic], depth: int, in_display_block: bool):
        for e in entities:
            try:
                self._handle(e, depth, in_display_block)
            except Exception:
                continue  # 壊れたエンティティは黙ってスキップ

    def _handle(self, e: DXFGraphic, depth: int, in_display_block: bool):
        etype = e.dxftype()
        if etype == "INSERT":
            if depth >= MAX_INSERT_DEPTH:
                return
            self._walk(e.virtual_entities(), depth + 1, in_display_block)
            return
        if etype in EXPLODE_TYPES:
            try:
                self._walk(e.virtual_entities(), depth + 1, True)
            except Exception:
                pass
            return
        if etype in ("TEXT", "MTEXT"):
            self._add_text(e, etype)
            return
        if etype in ("POINT", "HATCH", "WIPEOUT", "IMAGE", "3DFACE", "ATTRIB", "ATTDEF"):
            return

        # 幾何エンティティ → display + (寸法ブロック内でなければ) segments
        pts = self._flatten_entity(e)
        if pts is None or len(pts) < 2:
            return
        self._add_display_path(e, pts)
        if etype in GEOMETRY_TYPES and not in_display_block:
            self._add_segments(e, etype, pts)

    # ------------------------------------------------------------- display

    def _entity_color(self, e: DXFGraphic) -> str:
        aci = e.dxf.get("color", 256)
        if aci == 256:  # BYLAYER
            return self._layer_colors.get(e.dxf.layer, "#c8c8c8")
        if aci == 0:    # BYBLOCK
            return "#c8c8c8"
        return _aci_to_hex(aci)

    def _flatten_entity(self, e: DXFGraphic) -> list[tuple[float, float]] | None:
        try:
            p = ezpath.make_path(e)
        except (TypeError, ValueError):
            return None
        pts = [(v.x, v.y) for v in p.flattening(FLATTEN_DIST)]
        return pts

    def _add_display_path(self, e: DXFGraphic, pts: list[tuple[float, float]]):
        closed = (abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9)
        self.display.append({
            "t": "p",
            "layer": e.dxf.layer,
            "color": self._entity_color(e),
            "pts": [(round(x, 4), round(y, 4)) for x, y in pts],
            "closed": closed,
        })

    def _add_text(self, e: DXFGraphic, etype: str):
        try:
            if etype == "MTEXT":
                text = e.plain_text()
                pos = e.dxf.insert
                height = e.dxf.char_height
                rot = e.dxf.rotation if e.dxf.hasattr("rotation") else 0.0
            else:
                text = e.dxf.text
                pos = e.dxf.insert
                height = e.dxf.height
                rot = e.dxf.get("rotation", 0.0)
        except Exception:
            return
        text = (text or "").strip()
        if not text:
            return
        self.display.append({
            "t": "t",
            "layer": e.dxf.layer,
            "color": self._entity_color(e),
            "x": round(pos.x, 4), "y": round(pos.y, 4),
            "h": round(float(height), 3),
            "rot": round(float(rot), 3),
            "text": text[:200],
        })

    # ------------------------------------------------------------ segments

    def _next_id(self) -> int:
        self._seg_id += 1
        return self._seg_id

    def _is_xy(self, e: DXFGraphic) -> bool:
        ez = e.dxf.get("extrusion", (0, 0, 1))
        return abs(ez[0]) < 1e-9 and abs(ez[1]) < 1e-9 and ez[2] > 0

    def _add_segments(self, e: DXFGraphic, etype: str, flat_pts: list[tuple[float, float]]):
        layer = e.dxf.layer
        if etype == "LINE":
            p0 = (e.dxf.start.x, e.dxf.start.y)
            p1 = (e.dxf.end.x, e.dxf.end.y)
            if _dist(p0, p1) > 1e-6:
                self.segments.append(Segment(self._next_id(), "line", layer, p0=p0, p1=p1))
            return
        if etype == "ARC" and self._is_xy(e):
            c = (e.dxf.center.x, e.dxf.center.y)
            seg = Segment(self._next_id(), "arc", layer,
                          center=c, radius=e.dxf.radius,
                          start_angle=e.dxf.start_angle, end_angle=e.dxf.end_angle)
            seg.p0 = seg.point_at(seg.start_angle)
            seg.p1 = seg.point_at(seg.end_angle)
            self.segments.append(seg)
            return
        if etype == "CIRCLE" and self._is_xy(e):
            c = (e.dxf.center.x, e.dxf.center.y)
            seg = Segment(self._next_id(), "circle", layer,
                          center=c, radius=e.dxf.radius, start_angle=0, end_angle=360)
            seg.p0 = seg.p1 = seg.point_at(0)
            self.segments.append(seg)
            return
        if etype in ("LWPOLYLINE", "POLYLINE"):
            # bulge 対応: LINE/ARC に展開して再帰処理
            try:
                for sub in e.virtual_entities():
                    st = sub.dxftype()
                    if st in ("LINE", "ARC", "CIRCLE"):
                        self._add_segments(sub, st, [])
            except Exception:
                pass
            return
        # ELLIPSE / SPLINE / OCS 外の ARC → 折れ線近似
        if len(flat_pts) >= 2:
            self.segments.append(Segment(
                self._next_id(), "poly", layer,
                p0=flat_pts[0], p1=flat_pts[-1], points=list(flat_pts)))

    # -------------------------------------------------------------- export

    _THICKNESS_PATTERNS = [
        r"(?:PL|ＰＬ)\s*[-‐]?\s*([0-9]+(?:\.[0-9]+)?)",   # PL2.3 / PL-9
        r"板厚\s*[=:=:]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bt\s*[=＝]?\s*([0-9]+(?:\.[0-9]+)?)",           # t=9 / t9
        r"([0-9]+(?:\.[0-9]+)?)\s*t\b",                    # 9t
    ]

    def suggest_thickness(self) -> float | None:
        """表題欄・注記テキストから板厚らしき値を推定する。"""
        import re
        import unicodedata
        for pattern in self._THICKNESS_PATTERNS:
            for d in self.display:
                if d["t"] != "t":
                    continue
                # 全角英数字 (ＰＬ２.３ 等) を半角に正規化してから照合
                text = unicodedata.normalize("NFKC", d["text"])
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    v = float(m.group(1))
                    if 0.1 <= v <= 300:
                        return v
        return None

    def to_json(self) -> dict:
        layers: dict[str, dict] = {}
        for d in self.display:
            info = layers.setdefault(d["layer"], {"count": 0, "geomCount": 0, "color": d["color"]})
            info["count"] += 1
        for s in self.segments:
            info = layers.setdefault(s.layer, {"count": 0, "geomCount": 0, "color": "#c8c8c8"})
            info["geomCount"] += 1
        xs, ys = [], []
        for d in self.display:
            if d["t"] == "p":
                for x, y in d["pts"]:
                    xs.append(x); ys.append(y)
        bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else [0, 0, 1, 1]
        return {
            "display": self.display,
            "layers": [{"name": k, **v} for k, v in sorted(layers.items())],
            "bbox": bbox,
            "suggestedThickness": self.suggest_thickness(),
        }


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
