export interface DisplayPath {
  t: "p";
  layer: string;
  color: string;
  pts: [number, number][];
  closed: boolean;
}

export interface DisplayText {
  t: "t";
  layer: string;
  color: string;
  x: number;
  y: number;
  h: number;
  rot: number;
  text: string;
}

export type DisplayEntity = DisplayPath | DisplayText;

export interface LayerInfo {
  name: string;
  count: number;
  geomCount: number;
  color: string;
}

export interface ParseResult {
  session: string;
  name: string;
  display: DisplayEntity[];
  layers: LayerInfo[];
  bbox: [number, number, number, number];
  suggestedThickness: number | null;
}

export interface LoopData {
  id: number;
  area: number;
  bbox: [number, number, number, number];
  poly: [number, number][];
  insideOf: number[];
  isCircle: boolean;
}

export interface BrowseResult {
  root: string;
  exists: boolean;
  dirs: { name: string; path: string }[];
  files: { name: string; path: string; size: number }[];
}

export interface ModelResult {
  step: string;
  glb: string;
  volume: number;
  bbox: [number, number, number];
}

export type ExtrudeMode = "up" | "down" | "mid";
