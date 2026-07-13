import type { LayerInfo, LoopData, ModelResult, ExtrudeMode } from "../types";

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
}

const STEEL_DENSITY = 7.85e-3; // g/mm^3

/** 右パネル: レイヤ / 選択状態 / 押し出し設定 / 出力 */
export default function SidePanel(p: Props) {
  const outer = p.loops.find((l) => l.id === p.selectedOuter);
  const massKg = p.result ? (p.result.volume * STEEL_DENSITY) / 1000 : null;

  return (
    <aside className="side-panel">
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
        {p.hasDoc && (
          <div className="hint" style={{ marginTop: 6 }}>
            図枠・寸法のレイヤを OFF にすると外形を選びやすくなります
          </div>
        )}
      </section>

      <section className="panel-sec">
        <h3>輪郭選択</h3>
        <button
          className="btn-ghost"
          style={{ width: "100%", marginBottom: 9, padding: "7px" }}
          disabled={p.loops.length === 0}
          onClick={p.onAutoSelect}
        >
          ⚡ 外形を自動選択 (図枠を除く最大輪郭)
        </button>
        <div className="stat-rows">
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
      </section>

      <section className="panel-sec">
        <h3>押し出し設定</h3>
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
          className="btn-primary"
          disabled={!outer || p.building || p.thickness <= 0}
          onClick={p.onBuild}
        >
          {p.building ? "生成中…" : "⚙ 3Dモデル生成"}
        </button>
      </section>

      {p.result && (
        <section className="panel-sec result">
          <h3>出力</h3>
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
