import type { BulkJob } from "../types";

interface Props {
  job: BulkJob;
}

const STATUS_LABEL: Record<string, [string, string]> = {
  built: ["✅ 3D化成功", "st-ok"],
  built_invalid: ["⚠ 検証警告", "st-warn"],
  interpreted_only: ["📋 解釈のみ (3D未生成)", "st-info"],
  build_failed: ["✗ ビルド失敗", "st-err"],
  error: ["✗ エラー", "st-err"],
  skipped_large: ["― スキップ(大)", "st-dim"],
};

/** 一括3D化の進捗・結果ビュー。生成中も他の作業を邪魔しない後追い確認用。 */
export default function BulkPanel({ job }: Props) {
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  const built = job.results.filter((r) => r.status === "built").length;

  return (
    <div className="bulk-panel">
      <div className="bulk-head">
        <div>
          <b>一括3D化: {job.label}</b>
          <span className="hint" style={{ marginLeft: 10 }}>
            {job.running
              ? `処理中… ${job.done}/${job.total} ${job.current ? `(現在: ${job.current})` : ""}`
              : `完了 — ${job.done}/${job.total} 件処理、3D化成功 ${built} 件`}
          </span>
        </div>
        <div className="hint">出力先: {job.out_dir}</div>
      </div>
      <div className="bulk-progress">
        <div className="bulk-progress-fill" style={{ width: `${pct}%` }} />
        <span className="bulk-progress-text">{pct}%</span>
      </div>
      <div className="hint" style={{ padding: "4px 2px 8px" }}>
        {job.running
          ? "このまま他の作業をしていて大丈夫です。処理はサーバー側で継続し、完了分から順次この一覧に追加されます。"
          : "全件の処理が終わりました。⚠仮定・❗矛盾の件数を確認し、STEPをダウンロードしてください。"}
      </div>
      <div className="bulk-table-wrap">
        <table className="bulk-table">
          <thead>
            <tr>
              <th>図面</th><th>結果</th><th>部品名</th><th>エンジン</th>
              <th>⚠仮定</th><th>❗矛盾</th><th>STEP</th>
            </tr>
          </thead>
          <tbody>
            {job.results.map((r, i) => {
              const [label, cls] = STATUS_LABEL[r.status] ?? [r.status, "st-dim"];
              const reason = r.reason ?? r.error ?? "";
              return (
                <tr key={i}>
                  <td title={r.name}>{r.name}</td>
                  <td className={cls} title={reason}>
                    {label}
                    {reason && r.status !== "built" && (
                      <div className="bulk-reason">{reason}</div>
                    )}
                    {r.cross_check_diffs && r.cross_check_diffs.length > 0 && (
                      <div className="bulk-reason st-warn">
                        🔍 第二意見と不一致: {r.cross_check_diffs.join(" / ")}
                      </div>
                    )}
                  </td>
                  <td>{r.part_name ?? "-"}</td>
                  <td className="mono">{(r.engine ?? "-").replace("claude-", "").replace("gemini-", "gem-")}
                    {r.fallback ? " (代替)" : ""}</td>
                  <td title={(r.assumptions ?? []).join("\n")}>{r.assumptions?.length ?? 0}</td>
                  <td title={(r.drawing_conflicts ?? []).join("\n")}>{r.drawing_conflicts?.length ?? 0}</td>
                  <td>
                    {r.step ? (
                      <a href={`/api/bulk/${job.id}/file/${encodeURIComponent(r.step)}`} download>⬇</a>
                    ) : "-"}
                  </td>
                </tr>
              );
            })}
            {job.results.length === 0 && (
              <tr><td colSpan={7} className="hint" style={{ textAlign: "center", padding: 18 }}>
                最初の1件を処理中です…
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
