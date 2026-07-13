import { useCallback, useEffect, useState } from "react";
import { browse } from "../api";
import type { BrowseResult } from "../types";

interface Props {
  onOpenPath: (path: string) => void;
  onUpload: (file: File) => void;
  currentName: string | null;
  busy: boolean;
  onBulkStart: (path: string, label: string) => void;
  onBulkStartFiles: (files: string[]) => void;
  bulkRunning: boolean;
  generatingNames: Set<string>;                 // いま生成中のファイル名
  bulkFileStatus: Record<string, string>;       // 今回の一括ジョブの処理結果 (name→status)
}

const BULK_MARK: Record<string, { icon: string; cls: string; label: string }> = {
  built: { icon: "✓", cls: "ok", label: "3D化成功" },
  built_invalid: { icon: "⚠", cls: "warn", label: "生成したが検証警告あり" },
  interpreted_only: { icon: "－", cls: "dim", label: "解釈のみ (自動3D化は未対応の形状)" },
  not_a_part: { icon: "－", cls: "dim", label: "対象外 (組立図・購入品等)" },
  build_failed: { icon: "✗", cls: "err", label: "ビルド失敗" },
  error: { icon: "✗", cls: "err", label: "エラー" },
  skipped_large: { icon: "－", cls: "dim", label: "スキップ (ファイルが大きすぎる)" },
};

/** 左パネル: サーバフォルダブラウズ + ドラッグ&ドロップ + 一括3D化 (フォルダ/選択ファイル) */
export default function FilePanel({ onOpenPath, onUpload, currentName, busy, onBulkStart, onBulkStartFiles, bulkRunning, generatingNames, bulkFileStatus }: Props) {
  const [path, setPath] = useState("");
  const [data, setData] = useState<BrowseResult | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // チェックした DXF (相対パス)。フォルダを移動しても選択は保持される
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const toggleCheck = (p: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  };

  const checkedInFolder = data?.files.filter((f) => checked.has(f.path)).length ?? 0;

  // 今見ているフォルダ内のチェックだけを解除 (他フォルダの選択は保持)
  const clearFolderChecks = () => {
    setChecked((prev) => {
      const next = new Set(prev);
      data?.files.forEach((f) => next.delete(f.path));
      return next;
    });
  };

  const allInFolderChecked =
    !!data?.files.length && data.files.every((f) => checked.has(f.path));
  const toggleFolderAll = () => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (allInFolderChecked) {
        data?.files.forEach((f) => next.delete(f.path));
      } else {
        data?.files.forEach((f) => next.add(f.path));
      }
      return next;
    });
  };

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

      {checked.size > 0 ? (
        <div className="bulk-select-box">
          <button
            className="btn-secondary bulk-selected"
            disabled={bulkRunning}
            onClick={() => onBulkStartFiles([...checked])}
            title="チェックしたDXFだけをバックグラウンドで順次3D化します"
          >
            {bulkRunning ? "一括3D化 実行中…" : `✅ 選択した ${checked.size} 件を一括3D化`}
          </button>
          <div className="clear-check-row">
            <button
              className="btn-ghost clear-check"
              disabled={checkedInFolder === 0}
              onClick={clearFolderChecks}
              title="今開いているフォルダ内のチェックだけを外します (他フォルダの選択は残ります)"
            >
              このフォルダの選択解除{checkedInFolder > 0 ? ` (${checkedInFolder})` : ""}
            </button>
            <button
              className="btn-ghost clear-check"
              onClick={() => setChecked(new Set())}
              title="全フォルダのチェックをすべて外します"
            >
              すべて解除
            </button>
          </div>
        </div>
      ) : (
        <button
          className="btn-secondary"
          disabled={bulkRunning || !data?.exists}
          onClick={() => onBulkStart(path, path || "部品表フォルダ全体")}
          title="このフォルダ内 (サブフォルダ含む) の全DXFを順次3D化します。特定のファイルだけ処理したい場合は、ファイル名の左のチェックを使ってください"
        >
          {bulkRunning ? "一括3D化 実行中…" : "📦 このフォルダを一括3D化"}
        </button>
      )}

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
        {!!data?.files.length && (
          <label className="file-select-all">
            <input type="checkbox" checked={allInFolderChecked} onChange={toggleFolderAll} />
            このフォルダの {data.files.length} 件を選択
          </label>
        )}
        {data?.files.map((f) => {
          const generating = generatingNames.has(f.name);
          const mark = !generating ? BULK_MARK[bulkFileStatus[f.name] ?? ""] : undefined;
          return (
            <div key={f.path} className="file-row">
              <input
                type="checkbox"
                className="fi-check"
                checked={checked.has(f.path)}
                onChange={() => toggleCheck(f.path)}
                disabled={bulkRunning}
                title="一括3D化の対象にする"
              />
              <button
                className={`file-item ${currentName === f.name ? "active" : ""} ${generating ? "generating" : ""}`}
                disabled={busy}
                onClick={() => onOpenPath(f.path)}
                title={generating ? `${f.name} — AI生成中…` : mark ? `${f.name} — ${mark.label}` : f.name}
              >
                <span className="fi-icon">📐</span>
                <span className="fi-name">{f.name}</span>
                {generating ? (
                  <span className="fi-gen" title="AI生成中">⚙</span>
                ) : mark ? (
                  <span className={`fi-mark ${mark.cls}`}>{mark.icon}</span>
                ) : (
                  <span className="fi-size">{fmtSize(f.size)}</span>
                )}
              </button>
            </div>
          );
        })}
        {data && data.dirs.length === 0 && data.files.length === 0 && data.exists && (
          <div className="hint" style={{ padding: 12 }}>DXF ファイルがありません</div>
        )}
      </div>
    </aside>
  );
}
