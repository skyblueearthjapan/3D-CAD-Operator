import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { aiInterpret, buildModel, bulkStart, bulkStatus, detectContours, getCachedResult, openFile, uploadFile } from "./api";
import type { AiResult, BulkJob, ExtrudeMode, LoopData, ModelResult, ParseResult } from "./types";
import BulkPanel from "./components/BulkPanel";
import FilePanel from "./components/FilePanel";
import SidePanel from "./components/SidePanel";
import Viewer2D from "./components/Viewer2D";
import Viewer3D from "./components/Viewer3D";
import "./App.css";

type Tab = "2d" | "3d" | "bulk";

export default function App() {
  const [doc, setDoc] = useState<ParseResult | null>(null);
  const [visibleLayers, setVisibleLayers] = useState<Set<string>>(new Set());
  const [loops, setLoops] = useState<LoopData[]>([]);
  const [selectedOuter, setSelectedOuter] = useState<number | null>(null);
  const [selectedHoles, setSelectedHoles] = useState<Set<number>>(new Set());
  const [thickness, setThickness] = useState(9);
  const [mode, setMode] = useState<ExtrudeMode>("up");
  const [result, setResult] = useState<ModelResult | null>(null);
  const [aiResult, setAiResult] = useState<AiResult | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiElapsed, setAiElapsed] = useState(0);
  const [regionMode, setRegionMode] = useState(false);
  const [region, setRegion] = useState<[number, number, number, number] | null>(null);
  const [bulkJob, setBulkJob] = useState<BulkJob | null>(null);
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
    setAiResult(null);
    setSelectedOuter(null);
    setSelectedHoles(new Set());
    setRegionMode(false);
    setRegion(null);
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
      const parsed = await openFile(path);
      await handleParsed(parsed);
      // 保存済みの生成結果 (前回の3Dモデル) があれば復元する
      try {
        const c = await getCachedResult(path);
        if (c.exists && c.result) {
          setAiResult(c.result);
          if (c.result.glb) {
            showToast("ok",
              `前回の生成結果を復元しました (${c.result.cached_at?.replace("T", " ") ?? "日時不明"} 生成)。再生成も可能です`);
          }
        }
      } catch {
        /* 復元失敗は無視 (通常フローに影響させない) */
      }
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
      if (r.valid === false) {
        showToast("err", "モデルを生成しましたが、ジオメトリ検証で警告があります。CAD での読み込みに問題があれば穴の選択を見直してください");
      } else {
        showToast("ok", "3D モデルを生成しました");
      }
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setBuilding(false);
    }
  }, [doc, selectedOuter, selectedHoles, thickness, mode]);

  // ---- AI 解釈 → 3D 生成 (メインフロー。reg指定時は範囲内のみ解釈)
  const onAiBuild = useCallback(async (reg?: [number, number, number, number] | null) => {
    if (!doc) return;
    setAiBusy(true);
    try {
      const r = await aiInterpret(doc.session, false, reg ?? null);
      setAiResult(r);
      if (r.buildable && r.glb) {
        setTab("3d");
        const n = r.spec.assumptions.length + r.spec.drawing_conflicts.length;
        showToast("ok", n > 0
          ? `3Dモデルを生成しました。仮定・矛盾が ${n} 件あります — 右パネルで確認してください`
          : "3Dモデルを生成しました");
      } else {
        showToast("err", "解釈は完了しましたが自動3D化は未対応の形状です (右パネル参照)");
      }
    } catch (e) {
      showToast("err", String(e));
    } finally {
      setAiBusy(false);
    }
  }, [doc]);

  // ---- 一括3D化 (フォルダ全体)
  const onBulkStart = useCallback(async (path: string, label: string) => {
    try {
      const r = await bulkStart(path);
      setBulkJob({
        id: r.job_id, label, total: r.total, done: 0, running: true,
        current: null, out_dir: r.out_dir, results: [], started_at: "",
      });
      setTab("bulk");
      showToast("ok", `一括3D化を開始しました (${r.total} 件)。処理中も他の作業ができます`);
    } catch (e) {
      showToast("err", String(e));
    }
  }, []);

  // ---- 一括3D化 (チェックしたファイルのみ)
  const onBulkStartFiles = useCallback(async (files: string[]) => {
    try {
      const r = await bulkStart("", files);
      setBulkJob({
        id: r.job_id, label: `選択${files.length}件`, total: r.total, done: 0,
        running: true, current: null, out_dir: r.out_dir, results: [], started_at: "",
      });
      setTab("bulk");
      showToast("ok", `選択した ${r.total} 件の一括3D化を開始しました。処理中も他の作業ができます`);
    } catch (e) {
      showToast("err", String(e));
    }
  }, []);

  // AI解釈中の経過秒カウント (フリーズしていないことを可視化)
  useEffect(() => {
    if (!aiBusy) { setAiElapsed(0); return; }
    const t = window.setInterval(() => setAiElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [aiBusy]);

  // 一括ジョブのポーリング (実行中のみ 4 秒ごと)
  useEffect(() => {
    if (!bulkJob?.running) return;
    const timer = window.setInterval(async () => {
      try {
        const j = await bulkStatus(bulkJob.id);
        setBulkJob(j);
        if (!j.running) {
          const built = j.results.filter((r) => r.status === "built").length;
          showToast("ok", `一括3D化が完了しました: ${built}/${j.total} 件を3D化`);
        }
      } catch {
        /* サーバー再起動等は次のポーリングで回復 */
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [bulkJob?.id, bulkJob?.running]);

  // ステップ進行状況
  const built = !!(aiResult?.buildable || result);
  const step = !doc ? 1 : !built ? 2 : 3;

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
          {["DXFを開く", "生成 (AI解釈)", "確認・STEP出力"].map((s, i) => (
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
          onBulkStart={onBulkStart}
          onBulkStartFiles={onBulkStartFiles}
          bulkRunning={!!bulkJob?.running}
        />

        <main className="center">
          <div className="tabbar">
            <button className={tab === "2d" ? "tab active" : "tab"} onClick={() => setTab("2d")}>
              図面 (2D)
            </button>
            <button
              className={tab === "3d" ? "tab active" : "tab"}
              onClick={() => setTab("3d")}
              disabled={!aiResult?.glb && !result}
            >
              モデル (3D)
            </button>
            {bulkJob && (
              <button
                className={tab === "bulk" ? "tab active" : "tab"}
                onClick={() => setTab("bulk")}
              >
                一括3D化 {bulkJob.running ? `(${bulkJob.done}/${bulkJob.total})` : "✓"}
              </button>
            )}
          </div>
          <div className="view-area">
            {tab === "2d" && doc && (regionMode || region) && (
              <div className="region-bar">
                {!region ? (
                  <span>🔲 解釈したい部品のビュー(複数可)をドラッグで囲んでください</span>
                ) : (
                  <>
                    <span>選択: {Math.round(region[2] - region[0])}×{Math.round(region[3] - region[1])} mm</span>
                    <button
                      className="btn-primary region-go"
                      disabled={aiBusy}
                      onClick={() => { setRegionMode(false); onAiBuild(region); }}
                    >
                      ⚙ この範囲をAI解釈
                    </button>
                    <button className="btn-ghost" onClick={() => { setRegion(null); setRegionMode(true); }}>
                      やり直し
                    </button>
                  </>
                )}
                <button className="btn-ghost" onClick={() => { setRegionMode(false); setRegion(null); }}>
                  ✕ やめる
                </button>
              </div>
            )}
            {busy && <div className="loading-overlay"><div className="spinner" />読み込み中…</div>}
            {aiBusy && (
              <div className="loading-overlay ai-working">
                <div className="spinner" />
                <div className="ai-working-title">
                  AIが図面を解釈しています… <b>{aiElapsed}秒経過</b>
                </div>
                <div className="ai-working-steps">
                  <span className={aiElapsed < 5 ? "on" : "done"}>① 図面をAI用データに変換</span>
                  <span className={aiElapsed >= 5 && aiElapsed < 60 ? "on" : aiElapsed >= 60 ? "done" : ""}>② 注記・寸法・多面図を読解中</span>
                  <span className={aiElapsed >= 60 ? "on" : ""}>③ 3Dソリッド生成・検証</span>
                </div>
                <div className="hint">
                  目安 30〜120秒。時間がかかる場合は図面が複雑なだけで、フリーズではありません。<br />
                  何枚もまとめて変換する場合は、左の「📦 一括3D化」なら待たずに他の作業ができます。
                </div>
              </div>
            )}
            {tab === "bulk" && bulkJob ? (
              <BulkPanel job={bulkJob} />
            ) : tab === "2d" ? (
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
                  regionMode={regionMode && !region}
                  region={region}
                  onRegionSelected={(r) => setRegion(r)}
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
              <Viewer3D glbUrl={aiResult?.glb ?? result?.glb ?? null} />
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
          onAiBuild={() => onAiBuild(null)}
          onRegionMode={() => { setRegion(null); setRegionMode(true); setTab("2d"); }}
          aiBusy={aiBusy}
          aiResult={aiResult}
        />
      </div>

      {toast && <div className={`toast ${toast.kind}`}>{toast.msg}</div>}
    </div>
  );
}
