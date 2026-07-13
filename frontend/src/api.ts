import type { BrowseResult, LoopData, ModelResult, ParseResult, ExtrudeMode } from "./types";

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
