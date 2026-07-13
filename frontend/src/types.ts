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
  files: { name: string; path: string; size: number; gen?: "3d" | "interpreted" | null }[];
}

export interface ModelResult {
  step: string;
  glb: string;
  volume: number;
  bbox: [number, number, number];
  valid: boolean;
}

export type ExtrudeMode = "up" | "down" | "mid";

// ---------------- AI 解釈 ----------------

export interface AiHole {
  x: number;
  y: number;
  diameter: number | null;
  thread: string | null;
  csk_diameter: number | null;
  cbore_diameter: number | null;
  cbore_depth: number | null;
  through: boolean;
  depth: number | null;
  from_face: string;
  axis: string;
  note: string | null;
}

export interface AiSpec {
  part_name: string;
  material: string | null;
  shape_class: string;
  thickness: number | null;
  outer_diameter: number | null;
  length: number | null;
  width: number | null;
  profile_points: number[][] | null;
  outer_stack: { diameter: number; height: number }[] | null;
  bore_stack: { diameter: number; height: number }[] | null;
  holes: AiHole[];
  chamfer_notes: string[];
  unmodeled_features: string[];
  unsupported_reason: string | null;
  assumptions: string[];
  drawing_conflicts: string[];
}

export interface AiVerification {
  brep_valid: boolean;
  volume_mm3: number;
  bbox: number[];
  dimension_warnings: string[];
}

export interface BulkRecord {
  name: string;
  status: string;
  engine?: string;
  fallback?: string;
  part_name?: string;
  shape_class?: string;
  assumptions?: string[];
  drawing_conflicts?: string[];
  verification?: AiVerification;
  step?: string;
  glb?: string;
  error?: string;
  reason?: string;
  cross_check_diffs?: string[];
  seconds?: number;
}

export interface BulkJob {
  id: string;
  label: string;
  total: number;
  done: number;
  running: boolean;
  current: string | null;
  out_dir: string;
  results: BulkRecord[];
  started_at: string;
}

export interface AiResult {
  spec: AiSpec;
  usage: {
    input_tokens: number;
    output_tokens: number;
    model: string;
    fallback_reason?: string;
  };
  gemini_cross_check: unknown;
  cross_check_diffs: string[];
  buildable: boolean;
  step: string | null;
  glb: string | null;
  verification: AiVerification | null;
  build_error: string | null;
  cross_check_auto: boolean;
  auto_fix?: string | null;
  region?: number[] | null; // 領域指定解釈の場合の選択範囲 [x0,y0,x1,y1]
  cached_at?: string;      // 保存済み結果を復元した場合の生成日時
  source_path?: string;
}
