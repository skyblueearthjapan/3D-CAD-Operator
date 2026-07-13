import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DisplayEntity, LoopData } from "../types";

interface Props {
  display: DisplayEntity[];
  visibleLayers: Set<string>;
  bbox: [number, number, number, number];
  loops: LoopData[];
  selectedOuter: number | null;
  selectedHoles: Set<number>;
  candidateHoles: Set<number>;
  onSelectOuter: (id: number) => void;
  onToggleHole: (id: number) => void;
  // 領域指定解釈: regionMode中はドラッグ=矩形選択 (パン無効)
  regionMode?: boolean;
  region?: [number, number, number, number] | null;
  onRegionSelected?: (r: [number, number, number, number]) => void;
}

interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 図面 2D ビューア: パン / ズーム / 輪郭クリック選択 */
export default function Viewer2D({
  display, visibleLayers, bbox, loops,
  selectedOuter, selectedHoles, candidateHoles,
  onSelectOuter, onToggleHole,
  regionMode = false, region = null, onRegionSelected,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [vb, setVb] = useState<ViewBox>({ x: 0, y: 0, w: 100, h: 100 });
  const [hovered, setHovered] = useState<number | null>(null);
  const drag = useRef<{ x: number; y: number; vb: ViewBox } | null>(null);
  const moved = useRef(false);
  const selStart = useRef<{ x: number; y: number } | null>(null);
  const [selRect, setSelRect] = useState<[number, number, number, number] | null>(null);

  const fit = useCallback(() => {
    const [x0, y0, x1, y1] = bbox;
    const w = Math.max(x1 - x0, 1);
    const h = Math.max(y1 - y0, 1);
    const pad = Math.max(w, h) * 0.04;
    // モデル座標は y 上向き → scale(1,-1) で描画するため viewBox の y は反転域
    setVb({ x: x0 - pad, y: -(y1 + pad), w: w + pad * 2, h: h + pad * 2 });
  }, [bbox]);

  useEffect(() => { fit(); }, [fit]);

  const clientToModel = useCallback((cx: number, cy: number) => {
    const svg = svgRef.current!;
    const r = svg.getBoundingClientRect();
    const px = vb.x + ((cx - r.left) / r.width) * vb.w;
    const py = vb.y + ((cy - r.top) / r.height) * vb.h;
    return { px, py };
  }, [vb]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    const factor = e.deltaY > 0 ? 1.18 : 1 / 1.18;
    const { px, py } = clientToModel(e.clientX, e.clientY);
    setVb(v => ({
      x: px - (px - v.x) * factor,
      y: py - (py - v.y) * factor,
      w: v.w * factor,
      h: v.h * factor,
    }));
  }, [clientToModel]);

  // クライアント座標 → 図面(モデル)座標 (viewBoxのyは反転域なので符号を戻す)
  const clientToDrawing = useCallback((cx: number, cy: number) => {
    const { px, py } = clientToModel(cx, cy);
    return { x: px, y: -py };
  }, [clientToModel]);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    if (regionMode) {
      selStart.current = clientToDrawing(e.clientX, e.clientY);
      setSelRect(null);
      return;
    }
    drag.current = { x: e.clientX, y: e.clientY, vb };
    moved.current = false;
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (regionMode && selStart.current) {
      const p = clientToDrawing(e.clientX, e.clientY);
      const s = selStart.current;
      setSelRect([Math.min(s.x, p.x), Math.min(s.y, p.y), Math.max(s.x, p.x), Math.max(s.y, p.y)]);
      return;
    }
    if (!drag.current) return;
    const svg = svgRef.current!;
    const r = svg.getBoundingClientRect();
    const dx = ((e.clientX - drag.current.x) / r.width) * drag.current.vb.w;
    const dy = ((e.clientY - drag.current.y) / r.height) * drag.current.vb.h;
    if (Math.abs(e.clientX - drag.current.x) + Math.abs(e.clientY - drag.current.y) > 3) {
      moved.current = true;
    }
    setVb({ ...drag.current.vb, x: drag.current.vb.x - dx, y: drag.current.vb.y - dy });
  };
  const onPointerUp = () => {
    if (regionMode && selStart.current) {
      if (selRect && selRect[2] - selRect[0] > 2 && selRect[3] - selRect[1] > 2) {
        onRegionSelected?.(selRect);
      }
      selStart.current = null;
      setSelRect(null);
      return;
    }
    drag.current = null;
  };

  const strokeScale = vb.w / 1000; // 表示幅基準の線幅

  const paths = useMemo(() => display.filter(
    (d): d is Extract<DisplayEntity, { t: "p" }> => d.t === "p" && visibleLayers.has(d.layer),
  ), [display, visibleLayers]);

  const texts = useMemo(() => display.filter(
    (d): d is Extract<DisplayEntity, { t: "t" }> => d.t === "t" && visibleLayers.has(d.layer),
  ), [display, visibleLayers]);

  const loopPath = (l: LoopData) =>
    "M" + l.poly.map(([x, y]) => `${x},${y}`).join("L") + "Z";

  const holeState = (id: number): "on" | "off" | null => {
    if (selectedOuter === null) return null;
    if (selectedHoles.has(id)) return "on";
    if (candidateHoles.has(id)) return "off";
    return null;
  };

  return (
    <div className="viewer2d">
      <svg
        ref={svgRef}
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        preserveAspectRatio="xMidYMid meet"
        style={regionMode ? { cursor: "crosshair" } : undefined}
      >
        <g transform="scale(1,-1)">
          {/* 図面エンティティ */}
          {paths.map((d, i) => (
            <polyline
              key={i}
              points={d.pts.map(([x, y]) => `${x},${y}`).join(" ")}
              fill="none"
              stroke={d.color === "#ffffff" ? "#4a5c72" : d.color}
              strokeWidth={0.8}
              vectorEffect="non-scaling-stroke"
              opacity={0.85}
            />
          ))}
          {/* テキスト (y 反転を戻す) */}
          {texts.map((d, i) => (
            <text
              key={`t${i}`}
              x={d.x}
              y={-d.y}
              fontSize={d.h}
              fill={d.color === "#ffffff" ? "#6b7a8d" : d.color}
              opacity={0.75}
              transform={`scale(1,-1) ${d.rot ? `rotate(${-d.rot} ${d.x} ${-d.y})` : ""}`}
              style={{ userSelect: "none" }}
            >
              {d.text}
            </text>
          ))}
          {/* 領域選択の矩形 (ドラッグ中 or 確定済み) */}
          {(selRect || region) && (() => {
            const rr = (selRect ?? region)!;
            return (
              <rect
                x={rr[0]} y={rr[1]} width={rr[2] - rr[0]} height={rr[3] - rr[1]}
                fill="rgba(47,111,191,0.08)"
                stroke="#2f6fbf"
                strokeWidth={1.6}
                strokeDasharray="6 4"
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
            );
          })()}
          {/* 輪郭オーバーレイ */}
          {loops.map(l => {
            const isOuter = l.id === selectedOuter;
            const hs = holeState(l.id);
            const isHover = l.id === hovered;
            let fill = "transparent";
            let stroke = "transparent";
            if (isOuter) { fill = "rgba(47,111,191,0.14)"; stroke = "#2f6fbf"; }
            else if (hs === "on") { fill = "rgba(192,90,78,0.22)"; stroke = "#c05a4e"; }
            else if (hs === "off") { fill = "rgba(255,255,255,0.03)"; stroke = "rgba(255,180,84,0.5)"; }
            if (isHover && !isOuter) fill = hs === "on" ? "rgba(255,110,90,0.4)" : "rgba(76,194,255,0.12)";
            return (
              <path
                key={`l${l.id}`}
                d={loopPath(l)}
                fill={fill}
                stroke={stroke}
                strokeWidth={isOuter || hs === "on" ? 1.6 : 1}
                strokeDasharray={hs === "off" ? "4 3" : undefined}
                vectorEffect="non-scaling-stroke"
                style={{ cursor: "pointer" }}
                fillRule="evenodd"
                onPointerEnter={() => setHovered(l.id)}
                onPointerLeave={() => setHovered(h => (h === l.id ? null : h))}
                onClick={(e) => {
                  if (moved.current || regionMode) return;
                  e.stopPropagation();
                  if (selectedOuter !== null && (candidateHoles.has(l.id) || selectedHoles.has(l.id))) {
                    onToggleHole(l.id);
                  } else {
                    onSelectOuter(l.id);
                  }
                }}
              />
            );
          })}
        </g>
      </svg>
      <div className="viewer2d-toolbar">
        <button className="btn-ghost" onClick={fit} title="全体表示">⛶ フィット</button>
        <span className="hint">
          ホイール: ズーム / ドラッグ: 移動 / クリック: 外形選択 → 穴の ON/OFF
        </span>
      </div>
    </div>
  );
}
