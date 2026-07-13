import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildModel, detectContours, openFile, uploadFile } from "./api";
import type { ExtrudeMode, LoopData, ModelResult, ParseResult } from "./types";
import FilePanel from "./components/FilePanel";
import SidePanel from "./components/SidePanel";
import Viewer2D from "./components/Viewer2D";
import Viewer3D from "./components/Viewer3D";
import "./App.css";

type Tab = "2d" | "3d";

export default function App() {
  const [doc, setDoc] = useState<ParseResult | null>(null);
  const [visibleLayers, setVisibleLayers] = useState<Set<string>>(new Set());
  const [loops, setLoops] = useState<LoopData[]>([]);
  const [selectedOuter, setSelectedOuter] = useState<number | null>(null);
  const [selectedHoles, setSelectedHoles] = useState<Set<number>>(new Set());
  const [thickness, setThickness] = useState(9);
  const [mode, setMode] = useState<ExtrudeMode>("up");
  const [result, setResult] = useState<ModelResult | null>(null);
  const [tab, setTab] = useState<Tab>("2d");
  const [busy, setBusy] = useState(false);
  const [building, setBuilding] = useState(false);
  const [toast, setToast] = useState<{ kind: "err" | "ok"; msg: string } | null>(null);
  const autoBuildRef = useRef(false);

  const showToast = (kind: "err" | "ok", msg: string) => {
    setToast({ kind, msg });
    window.setTimeout(() => setToast(null), 5000);
  };

  // ---- 外形の自動推定: 図枠 (図面全体とほぼ同サイズのループ) を除いた最大ループ
  const pickOuterAuto = (lps: LoopData[], bbox: [number, number, number, number]) => {
    if (lps.length === 0) return null;
    const docArea = Math.max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1);
    const cand = lps.filter((l) => l.area < docArea * 0.75);
    return (cand.length ? cand : lps)[0];
  };

  const applyOuter = useCallback((id: number, lps: LoopData[]): number[] => {
    setSelectedOuter(id);
    const insideOuter = new Set(
      lps.filter((l) => l.insideOf.includes(id)).map((l) => l.id),
    );
    const direct = lps
      .filter((l) => insideOuter.has(l.id) && !l.insideOf.some((m) => insideOuter.has(m)))
      .map((l) => l.id);
    setSelectedHoles(new Set(direct));
    return direct;
  }, []);

  // ---- ファイルオープン
  const handleParsed = useCallback(async (r: ParseResult) => {
    setDoc(r);
    setResult(null);
    setSelectedOuter(null);
    setSelectedHoles(new Set());
    setTab("2d");
    const all = new Set(r.layers.map((l) => l.name));
    setVisibleLayers(all);
    if (r.suggestedThickness) {
      setThickness(r.suggestedThickness);
      showToast("ok", `図面の注記から板厚 ${r.suggestedThickness} mm を検出しました`);
    }
    try {
      const lps = await detectContours(r.session, [...all]);
      setLoops(lps);
      const auto = pickOuterAuto(lps, r.bbox);
      const holes = auto ? applyOuter(auto.id, lps) : [];
      if (auto && autoBuildRef.current) {
        autoBuildRef.current = false;
        setBuilding(true);
        try {
          const res = await buildModel(
            r.session, auto.id, holes, r.suggestedThickness ?? 9, "up");
          setResult(res);
          setTab("3d");
        } finally {
          setBuilding(false);
        }
      }
    } catch (e) {
      setLoops([]);
      showToast("err", `輪郭検出に失敗: ${e}`);
    }
  }, [applyOuter]);

  const onOpenPath = useCallback(async (path: string) => {
    setBusy(true);
    try {
      handleParsed(await openFile(path));
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setBusy(false);
    }
  }, [handleParsed]);

  const onUpload = useCallback(async (file: File) => {
    setBusy(true);
    try {
      handleParsed(await uploadFile(file));
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setBusy(false);
    }
  }, [handleParsed]);

  // ---- レイヤ切替 → 輪郭再検出
  const onToggleLayer = useCallback(async (name: string) => {
    if (!doc) return;
    const next = new Set(visibleLayers);
    if (next.has(name)) next.delete(name); else next.add(name);
    setVisibleLayers(next);
    setSelectedOuter(null);
    setSelectedHoles(new Set());
    try {
      setLoops(await detectContours(doc.session, [...next]));
    } catch (e) {
      showToast("err", `輪郭検出に失敗: ${e}`);
    }
  }, [doc, visibleLayers]);

  // ---- 外形選択 → 直下の穴を自動選択
  const candidateHoles = useMemo(() => {
    if (selectedOuter === null) return new Set<number>();
    const insideOuter = new Set(
      loops.filter((l) => l.insideOf.includes(selectedOuter)).map((l) => l.id),
    );
    // 直下 (他の内側ループに含まれない) のみ穴候補
    return new Set(
      loops
        .filter((l) => insideOuter.has(l.id) && !l.insideOf.some((m) => insideOuter.has(m)))
        .map((l) => l.id),
    );
  }, [loops, selectedOuter]);

  const onSelectOuter = useCallback((id: number) => {
    applyOuter(id, loops);
  }, [applyOuter, loops]);

  const onAutoSelect = useCallback(() => {
    if (!doc) return;
    const auto = pickOuterAuto(loops, doc.bbox);
    if (auto) applyOuter(auto.id, loops);
  }, [doc, loops, applyOuter]);

  const onToggleHole = useCallback((id: number) => {
    setSelectedHoles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  // ---- 3D 生成
  const onBuild = useCallback(async () => {
    if (!doc || selectedOuter === null) return;
    setBuilding(true);
    try {
      const r = await buildModel(doc.session, selectedOuter, [...selectedHoles], thickness, mode);
      setResult(r);
      setTab("3d");
      showToast("ok", "3D モデルを生成しました");
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setBuilding(false);
    }
  }, [doc, selectedOuter, selectedHoles, thickness, mode]);

  // ステップ進行状況
  const step = !doc ? 1 : selectedOuter === null ? 2 : !result ? 3 : 4;

  useEffect(() => {
    document.title = doc ? `${doc.name} — DXF→STEP` : "DXF → STEP 変換システム";
  }, [doc]);

  // ディープリンク: ?open=<DXF_ROOT からの相対パス>  (&build=1 で自動変換)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const p = params.get("open");
    if (params.get("build") === "1") autoBuildRef.current = true;
    if (p) onOpenPath(p);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">▲</span>
          DXF <span className="arrow">→</span> STEP <span className="brand-sub">変換システム</span>
        </div>
        <div className="steps">
          {["DXFを開く", "外形を選択", "板厚を設定", "STEP出力"].map((s, i) => (
            <div key={s} className={`step ${step > i + 1 ? "done" : step === i + 1 ? "now" : ""}`}>
              <span className="step-n">{step > i + 1 ? "✓" : i + 1}</span>{s}
            </div>
          ))}
        </div>
        <div className="doc-name">{doc?.name ?? ""}</div>
      </header>

      <div className="body">
        <FilePanel
          onOpenPath={onOpenPath}
          onUpload={onUpload}
          currentName={doc?.name ?? null}
          busy={busy}
        />

        <main className="center">
          <div className="tabbar">
            <button className={tab === "2d" ? "tab active" : "tab"} onClick={() => setTab("2d")}>
              図面 (2D)
            </button>
            <button
              className={tab === "3d" ? "tab active" : "tab"}
              onClick={() => setTab("3d")}
              disabled={!result}
            >
              モデル (3D)
            </button>
          </div>
          <div className="view-area">
            {busy && <div className="loading-overlay"><div className="spinner" />読み込み中…</div>}
            {tab === "2d" ? (
              doc ? (
                <Viewer2D
                  display={doc.display}
                  visibleLayers={visibleLayers}
                  bbox={doc.bbox}
                  loops={loops}
                  selectedOuter={selectedOuter}
                  selectedHoles={selectedHoles}
                  candidateHoles={candidateHoles}
                  onSelectOuter={onSelectOuter}
                  onToggleHole={onToggleHole}
                />
              ) : (
                <div className="welcome">
                  <div className="welcome-mark">📐 → 📦</div>
                  <h2>DXF 図面から STEP (3D CAD) を作成</h2>
                  <p>
                    左のフォルダから図面を選ぶか、DXF ファイルをドロップしてください。<br />
                    閉じた輪郭を自動検出し、外形をクリック → 板厚を入力するだけで STEP を出力します。
                  </p>
                </div>
              )
            ) : (
              <Viewer3D glbUrl={result?.glb ?? null} />
            )}
          </div>
        </main>

        <SidePanel
          layers={doc?.layers ?? []}
          visibleLayers={visibleLayers}
          onToggleLayer={onToggleLayer}
          loops={loops}
          selectedOuter={selectedOuter}
          selectedHoles={selectedHoles}
          onAutoSelect={onAutoSelect}
          thickness={thickness}
          setThickness={setThickness}
          mode={mode}
          setMode={setMode}
          onBuild={onBuild}
          building={building}
          result={result}
          hasDoc={!!doc}
        />
      </div>

      {toast && <div className={`toast ${toast.kind}`}>{toast.msg}</div>}
    </div>
  );
}
