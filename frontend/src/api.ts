import type { AiResult, BrowseResult, BulkJob, LoopData, ModelResult, ParseResult, ExtrudeMode } from "./types";

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export async function browse(path: string): Promise<BrowseResult> {
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  return jsonOrThrow(res);
}

export async function openFile(path: string): Promise<ParseResult> {
  const res = await fetch("/api/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return jsonOrThrow(res);
}

export async function uploadFile(file: File): Promise<ParseResult> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  return jsonOrThrow(res);
}

export async function detectContours(session: string, layers: string[]): Promise<LoopData[]> {
  const res = await fetch("/api/contours", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, layers }),
  });
  const data = await jsonOrThrow<{ loops: LoopData[] }>(res);
  return data.loops;
}

export async function getCachedResult(
  path: string,
): Promise<{ exists: boolean; result?: AiResult }> {
  const res = await fetch(`/api/cached_result?path=${encodeURIComponent(path)}`);
  return jsonOrThrow(res);
}

export async function aiInterpret(
  session: string,
  crossCheck: boolean,
  region?: [number, number, number, number] | null,
): Promise<AiResult> {
  const res = await fetch("/api/ai_interpret", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, cross_check: crossCheck, region: region ?? null }),
  });
  return jsonOrThrow(res);
}

export async function bulkStart(
  path: string,
  files?: string[],
): Promise<{ job_id: string; total: number; out_dir: string }> {
  const res = await fetch("/api/bulk_start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(files && files.length ? { files } : { path, recursive: true }),
  });
  return jsonOrThrow(res);
}

export async function bulkStatus(jobId: string): Promise<BulkJob> {
  const res = await fetch(`/api/bulk/${jobId}`);
  return jsonOrThrow(res);
}

export async function buildModel(
  session: string,
  outer: number,
  holes: number[],
  thickness: number,
  mode: ExtrudeMode,
): Promise<ModelResult> {
  const res = await fetch("/api/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, outer, holes, thickness, mode }),
  });
  return jsonOrThrow(res);
}
