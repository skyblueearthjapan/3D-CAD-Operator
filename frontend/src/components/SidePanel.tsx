import type { AiResult, LayerInfo, LoopData, ModelResult, ExtrudeMode } from "../types";

interface Props {
  layers: LayerInfo[];
  visibleLayers: Set<string>;
  onToggleLayer: (name: string) => void;
  loops: LoopData[];
  selectedOuter: number | null;
  selectedHoles: Set<number>;
  onAutoSelect: () => void;
  thickness: number;
  setThickness: (v: number) => void;
  mode: ExtrudeMode;
  setMode: (m: ExtrudeMode) => void;
  onBuild: () => void;
  building: boolean;
  result: ModelResult | null;
  hasDoc: boolean;
  // AI 解釈
  onAiBuild: () => void;
  onRegionMode: () => void;
  aiBusy: boolean;
  aiResult: AiResult | null;
}

const STEEL_DENSITY = 7.85e-3; // g/mm^3

const SHAPE_LABEL: Record<string, string> = {
  circular_plate: "円板",
  rect_plate: "矩形板",
  profile_plate: "プロファイル板",
  revolved: "旋盤物 (段付き円筒)",
  unsupported: "自動ビルド未対応形状",
};

function holeLabel(h: AiResult["spec"]["holes"][number]): string {
  const parts: string[] = [];
  if (h.thread) parts.push(h.thread);
  else if (h.diameter) parts.push(`φ${h.diameter}`);
  if (h.csk_diameter) parts.push(`皿φ${h.csk_diameter}`);
  if (h.cbore_diameter) parts.push(`ザグリφ${h.cbore_diameter}×${h.cbore_depth ?? "?"}`);
  parts.push(h.through ? "貫通" : `深さ${h.depth ?? "?"}`);
  if (h.axis === "y") parts.push("端面");
  return parts.join(" ");
}

/** 右パネル: AI解釈生成 / 解釈結果 / レイヤ / 手動生成 */
export default function SidePanel(p: Props) {
  const outer = p.loops.find((l) => l.id === p.selectedOuter);
  const massKg = p.result ? (p.result.volume * STEEL_DENSITY) / 1000 : null;
  const ai = p.aiResult;
  const aiMassKg = ai?.verification
    ? (ai.verification.volume_mm3 * STEEL_DENSITY) / 1000
    : null;

  return (
    <aside className="side-panel">
      {/* ---------------- AI 生成 (メインフロー) ---------------- */}
      <section className="panel-sec">
        <h3>3Dモデル生成<span className="ai-badge">AI解釈</span></h3>
        <div className="hint" style={{ marginBottom: 8 }}>
          図面の注記・寸法・多面図をAI (Claude) が読み取り、
          板厚・穴・皿ザグリ等を解釈して3D化します。
        </div>
        <button
          className="btn-primary"
          disabled={!p.hasDoc || p.aiBusy}
          onClick={p.onAiBuild}
        >
          {p.aiBusy ? "AI解釈中… (30〜90秒)" : "⚙ 生成 (AI解釈)"}
        </button>
        <button
          className="btn-secondary"
          style={{ marginTop: 6 }}
          disabled={!p.hasDoc || p.aiBusy}
          onClick={p.onRegionMode}
          title="複数の部品が1枚に描かれた図面などで、解釈したい部品のビューだけをドラッグで囲んで指定します"
        >
          🔲 範囲を選んでAI解釈
        </button>
      </section>

      {/* ---------------- AI 解釈結果 ---------------- */}
      {ai && (
        <section className="panel-sec result">
          <h3>AI解釈結果</h3>
          <div className="stat-rows">
            <div className="stat-row">
              <span>解釈エンジン</span>
              <b>{ai.usage.model}{ai.usage.fallback_reason ? " (代替)" : ""}</b>
            </div>
            <div className="stat-row"><span>部品名</span><b>{ai.spec.part_name}</b></div>
            <div className="stat-row">
              <span>形状</span>
              <b>{SHAPE_LABEL[ai.spec.shape_class] ?? ai.spec.shape_class}</b>
            </div>
            {ai.spec.material && (
              <div className="stat-row"><span>材質</span><b>{ai.spec.material}</b></div>
            )}
            {ai.spec.thickness != null && (
              <div className="stat-row"><span>板厚</span><b>{ai.spec.thickness} mm</b></div>
            )}
            {ai.spec.outer_diameter != null && (
              <div className="stat-row"><span>外径</span><b>φ{ai.spec.outer_diameter}</b></div>
            )}
            {ai.spec.length != null && ai.spec.width != null && (
              <div className="stat-row">
                <span>外形</span><b>{ai.spec.length} × {ai.spec.width} mm</b>
              </div>
            )}
            <div className="stat-row"><span>穴</span><b>{ai.spec.holes.length} 件</b></div>
          </div>

          {ai.usage.fallback_reason && (
            <div className="ai-item diff" style={{ marginTop: 6 }}>
              {ai.usage.fallback_reason}
            </div>
          )}

          {ai.spec.holes.length > 0 && (
            <>
              <div className="ai-sub">穴の内訳</div>
              <div className="ai-list">
                {ai.spec.holes.slice(0, 8).map((h, i) => (
                  <div key={i} className="ai-item">
                    ({h.x.toFixed(1)}, {h.y.toFixed(1)}) {holeLabel(h)}
                  </div>
                ))}
                {ai.spec.holes.length > 8 && (
                  <div className="hint">…他 {ai.spec.holes.length - 8} 件</div>
                )}
              </div>
            </>
          )}

          {ai.spec.drawing_conflicts.length > 0 && (
            <>
              <div className="ai-sub">❗ 図面内の矛盾 (要確認)</div>
              <div className="ai-list">
                {ai.spec.drawing_conflicts.map((c, i) => (
                  <div key={i} className="ai-item conflict">{c}</div>
                ))}
              </div>
            </>
          )}

          {ai.spec.assumptions.length > 0 && (
            <>
              <div className="ai-sub">⚠ AIが置いた仮定 (要確認)</div>
              <div className="ai-list">
                {ai.spec.assumptions.map((a, i) => (
                  <div key={i} className="ai-item warn">{a}</div>
                ))}
              </div>
            </>
          )}

          {ai.cross_check_diffs.length > 0 && (
            <>
              <div className="ai-sub">
                🔍 第二意見(Gemini)との不一致 (要確認)
                {ai.cross_check_auto ? " ※仮定が多いため自動チェック" : ""}
              </div>
              <div className="ai-list">
                {ai.cross_check_diffs.map((d, i) => (
                  <div key={i} className="ai-item diff">{d}</div>
                ))}
              </div>
            </>
          )}

          {ai.spec.unmodeled_features.length > 0 && (
            <>
              <div className="ai-sub">未モデル化の特徴</div>
              <div className="ai-list">
                {ai.spec.unmodeled_features.map((f, i) => (
                  <div key={i} className="ai-item">{f}</div>
                ))}
              </div>
            </>
          )}

          {ai.buildable && ai.verification ? (
            <>
              <div className="ai-sub">検証</div>
              <div className="stat-rows" style={{ marginTop: 6 }}>
                <div className="stat-row">
                  <span>ジオメトリ検証</span>
                  <b className={ai.verification.brep_valid ? "verify-ok" : "verify-ng"}>
                    {ai.verification.brep_valid ? "✓ 正常" : "✗ 警告あり"}
                  </b>
                </div>
                <div className="stat-row">
                  <span>サイズ</span>
                  <b>{ai.verification.bbox.map((v) => v.toFixed(1)).join(" × ")} mm</b>
                </div>
                <div className="stat-row">
                  <span>体積</span><b>{(ai.verification.volume_mm3 / 1000).toFixed(1)} cm³</b>
                </div>
                {aiMassKg != null && (
                  <div className="stat-row">
                    <span>質量 (SS400)</span><b>{aiMassKg.toFixed(2)} kg</b>
                  </div>
                )}
              </div>
              {ai.verification.dimension_warnings.map((wmsg, i) => (
                <div key={i} className="ai-item warn" style={{ marginTop: 6 }}>{wmsg}</div>
              ))}
              {ai.step && (
                <a className="btn-download" href={ai.step} download>
                  ⬇ STEP ダウンロード
                </a>
              )}
            </>
          ) : (
            <div style={{ marginTop: 10 }}>
              <div className="no3d-banner">❌ 3Dデータは生成されていません(解釈のみ)</div>
              {ai.spec.unsupported_reason && (
                <div className="ai-item warn" style={{ marginTop: 6 }}>
                  <b>AIによる理由: </b>{ai.spec.unsupported_reason}
                </div>
              )}
              <div className="error-box" style={{ marginTop: 6 }}>
                {ai.build_error}
                <br />→ 手動モードで生成するか、上の解釈内容を参考にCADで作成してください。
              </div>
            </div>
          )}
        </section>
      )}

      {/* ---------------- レイヤ ---------------- */}
      <section className="panel-sec">
        <h3>レイヤ</h3>
        {p.layers.length === 0 && <div className="hint">DXF を開いてください</div>}
        <div className="layer-list">
          {p.layers.map((l) => (
            <label key={l.name} className="layer-row">
              <input
                type="checkbox"
                checked={p.visibleLayers.has(l.name)}
                onChange={() => p.onToggleLayer(l.name)}
              />
              <span className="layer-chip" style={{ background: l.color }} />
              <span className="layer-name">{l.name}</span>
              <span className="layer-count">{l.count}</span>
            </label>
          ))}
        </div>
      </section>

      {/* ---------------- 手動モード ---------------- */}
      <section className="panel-sec">
        <h3>手動モード (輪郭選択で生成)</h3>
        <button
          className="btn-ghost"
          style={{ width: "100%", marginBottom: 9, padding: "7px" }}
          disabled={p.loops.length === 0}
          onClick={p.onAutoSelect}
        >
          ⚡ 外形を自動選択 (図枠を除く最大輪郭)
        </button>
        <div className="stat-rows" style={{ marginBottom: 10 }}>
          <div className="stat-row">
            <span>検出された閉輪郭</span><b>{p.loops.length}</b>
          </div>
          <div className="stat-row">
            <span>外形</span>
            <b className={outer ? "ok" : ""}>
              {outer ? `#${outer.id} (${(outer.bbox[2] - outer.bbox[0]).toFixed(0)}×${(outer.bbox[3] - outer.bbox[1]).toFixed(0)}mm)` : "未選択"}
            </b>
          </div>
          <div className="stat-row">
            <span>穴</span><b>{p.selectedHoles.size} 個</b>
          </div>
        </div>
        <label className="field">
          <span>板厚 [mm]</span>
          <input
            type="number"
            min={0.1}
            step={0.5}
            value={p.thickness}
            onChange={(e) => p.setThickness(Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>方向</span>
          <select value={p.mode} onChange={(e) => p.setMode(e.target.value as ExtrudeMode)}>
            <option value="up">+Z (手前)</option>
            <option value="down">-Z (奥)</option>
            <option value="mid">両側均等</option>
          </select>
        </label>
        <button
          className="btn-secondary"
          disabled={!outer || p.building || p.thickness <= 0}
          onClick={p.onBuild}
        >
          {p.building ? "生成中…" : "手動で3Dモデル生成"}
        </button>
      </section>

      {p.result && (
        <section className="panel-sec result">
          <h3>手動生成の出力</h3>
          <div className="stat-rows">
            <div className="stat-row">
              <span>サイズ</span>
              <b>{p.result.bbox.map((v) => v.toFixed(1)).join(" × ")} mm</b>
            </div>
            <div className="stat-row">
              <span>体積</span><b>{(p.result.volume / 1000).toFixed(1)} cm³</b>
            </div>
            <div className="stat-row">
              <span>質量 (SS400)</span><b>{massKg!.toFixed(2)} kg</b>
            </div>
          </div>
          <a className="btn-download" href={p.result.step} download>
            ⬇ STEP ダウンロード
          </a>
        </section>
      )}
    </aside>
  );
}
