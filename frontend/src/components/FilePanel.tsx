import { useCallback, useEffect, useState } from "react";
import { browse } from "../api";
import type { BrowseResult } from "../types";

interface Props {
  onOpenPath: (path: string) => void;
  onUpload: (file: File) => void;
  currentName: string | null;
  busy: boolean;
  onBulkStart: (path: string, label: string) => void;
  bulkRunning: boolean;
}

/** 左パネル: サーバフォルダブラウズ + ドラッグ&ドロップ + 一括3D化 */
export default function FilePanel({ onOpenPath, onUpload, currentName, busy, onBulkStart, bulkRunning }: Props) {
  const [path, setPath] = useState("");
  const [data, setData] = useState<BrowseResult | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (p: string) => {
    try {
      setError(null);
      const r = await browse(p);
      setData(r);
      setPath(p);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => { load(""); }, [load]);

  const up = () => {
    if (!path) return;
    const idx = path.replace(/\\/g, "/").lastIndexOf("/");
    load(idx < 0 ? "" : path.slice(0, idx));
  };

  const fmtSize = (n: number) =>
    n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`;

  return (
    <aside className="file-panel">
      <div
        className={`dropzone ${dragOver ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files[0];
          if (f) onUpload(f);
        }}
        onClick={() => {
          const input = document.createElement("input");
          input.type = "file";
          input.accept = ".dxf";
          input.onchange = () => input.files?.[0] && onUpload(input.files[0]);
          input.click();
        }}
      >
        <span className="drop-icon">⬇</span>
        DXF をドロップ<br /><small>またはクリックして選択</small>
      </div>

      <div className="browser-head">
        <button className="btn-ghost" onClick={up} disabled={!path} title="上のフォルダへ">←</button>
        <span className="browser-path" title={path || "(ルート)"}>
          {path || "部品表フォルダ"}
        </span>
        <button className="btn-ghost" onClick={() => load(path)} title="再読込">⟳</button>
      </div>

      <button
        className="btn-secondary"
        disabled={bulkRunning || !data?.exists}
        onClick={() => onBulkStart(path, path || "部品表フォルダ全体")}
        title="このフォルダ内 (サブフォルダ含む) の全DXFをバックグラウンドで順次3D化します"
      >
        {bulkRunning ? "一括3D化 実行中…" : "📦 このフォルダを一括3D化"}
      </button>

      {error && <div className="error-box">{error}</div>}
      {data && !data.exists && (
        <div className="error-box">サーバの DXF フォルダが見つかりません (環境変数 DXF_ROOT を確認)</div>
      )}

      <div className="file-list">
        {data?.dirs.map((d) => (
          <button key={d.path} className="file-item dir" onClick={() => load(d.path)}>
            <span className="fi-icon">📁</span>
            <span className="fi-name">{d.name}</span>
          </button>
        ))}
        {data?.files.map((f) => (
          <button
            key={f.path}
            className={`file-item ${currentName === f.name ? "active" : ""}`}
            disabled={busy}
            onClick={() => onOpenPath(f.path)}
            title={f.name}
          >
            <span className="fi-icon">📐</span>
            <span className="fi-name">{f.name}</span>
            <span className="fi-size">{fmtSize(f.size)}</span>
          </button>
        ))}
        {data && data.dirs.length === 0 && data.files.length === 0 && data.exists && (
          <div className="hint" style={{ padding: 12 }}>DXF ファイルがありません</div>
        )}
      </div>
    </aside>
  );
}
