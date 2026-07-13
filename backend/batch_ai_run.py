# -*- coding: utf-8 -*-
"""全DXFに対するAI解釈バッチ (再開可能)。

使い方 (backend ディレクトリで):
    python batch_ai_run.py            # 未処理分を時間予算内で処理 (再実行で続きから)
    python batch_ai_run.py --report   # 集計レポートのみ生成

- 結果:   3D化トライアル/バッチ結果/results/<slug>.json
- モデル: 3D化トライアル/バッチ結果/models/*.step|.glb
- レポート: 3D化トライアル/バッチ結果/レポート.md
"""
import io
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ai_interpreter import (ShapeSpec, SpecBuildError, build_from_spec,
                                generate_dump, interpret, verify)
from app.dxf_parser import DxfDocument

DXF_ROOT = Path(os.environ.get(
    "DXF_ROOT",
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\DXFデータ 部品表用"))
OUT = Path(r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D化トライアル\バッチ結果")
RESULTS = OUT / "results"
MODELS = OUT / "models"
RESULTS.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)

BUDGET_SEC = 460          # この秒数を超えたら新規着手をやめる (再実行で続きから)
MAX_MB = 10               # これより大きいDXFはスキップ (組立図・レイアウト等)
WORKERS = 4


def slug(p: Path) -> str:
    rel = p.relative_to(DXF_ROOT)
    return re.sub(r"[\\/:*?\"<>| ]", "_", str(rel))[:120]


def process(p: Path) -> dict:
    rec: dict = {"file": str(p.relative_to(DXF_ROOT)), "name": p.name}
    t0 = time.time()
    try:
        if p.stat().st_size > MAX_MB * 1024 * 1024:
            rec["status"] = "skipped_large"
            return rec
        doc = DxfDocument(str(p))
        dump = generate_dump(doc)
        rec["dump_chars"] = len(dump)
        spec, usage = interpret(dump)
        rec["spec"] = spec.model_dump()
        rec["usage"] = usage
        try:
            solid = build_from_spec(spec)
            v = verify(solid, spec, doc)
            rec["verification"] = v
            from build123d import export_gltf, export_step
            base = MODELS / slug(p).replace(".dxf", "")
            export_step(solid, str(base) + "_AI.step")
            export_gltf(solid, str(base) + "_AI.glb", binary=True)
            rec["status"] = "built" if v["brep_valid"] else "built_invalid"
        except SpecBuildError as e:
            rec["status"] = "interpreted_only"
            rec["build_error"] = str(e)
        except Exception as e:
            rec["status"] = "build_failed"
            rec["build_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        rec["trace"] = traceback.format_exc()[-1000:]
    finally:
        rec["seconds"] = round(time.time() - t0, 1)
    return rec


def make_report():
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in RESULTS.glob("*.json")]
    recs.sort(key=lambda r: r["file"])
    by = {}
    for r in recs:
        by.setdefault(r["status"], []).append(r)
    total = len(recs)
    built = len(by.get("built", []))
    interp = len(by.get("interpreted_only", []))
    lines = [
        "# AI解釈バッチ結果", "",
        f"対象: {total} 図面 (DXF_ROOT: {DXF_ROOT})", "",
        "| 結果 | 件数 | 割合 |", "|---|---|---|",
        f"| ✅ 自動3D化成功 (BRepCheck valid) | {built} | {built/total*100:.0f}% |" if total else "",
        f"| 📋 解釈のみ (未対応形状クラス) | {interp} | {interp/total*100:.0f}% |" if total else "",
    ]
    for st in ("built_invalid", "build_failed", "error", "skipped_large"):
        n = len(by.get(st, []))
        if n:
            label = {"built_invalid": "⚠ 生成したが検証警告", "build_failed": "✗ ビルド失敗",
                     "error": "✗ 解釈/解析エラー", "skipped_large": f"― スキップ (>{MAX_MB}MB)"}[st]
            lines.append(f"| {label} | {n} | {n/total*100:.0f}% |")
    lines += ["", "## 明細", "",
              "| 図面 | 結果 | 形状クラス | 部品名 | 仮定 | 矛盾 | 備考 |", "|---|---|---|---|---|---|---|"]
    for r in recs:
        sp = r.get("spec", {})
        note = r.get("build_error") or r.get("error") or ""
        v = r.get("verification") or {}
        if r["status"] == "built":
            note = f"vol={v.get('volume_mm3', 0):,.0f}mm3"
            if v.get("dimension_warnings"):
                note += " ⚠" + ";".join(v["dimension_warnings"])
        lines.append(
            f"| {r['file']} | {r['status']} | {sp.get('shape_class','-')} | "
            f"{sp.get('part_name','-')} | {len(sp.get('assumptions',[]))} | "
            f"{len(sp.get('drawing_conflicts',[]))} | {str(note)[:80]} |")
    # トークン集計
    tin = sum(r.get("usage", {}).get("input_tokens", 0) for r in recs)
    tout = sum(r.get("usage", {}).get("output_tokens", 0) for r in recs)
    cost = tin / 1e6 * 5 + tout / 1e6 * 25
    lines += ["", f"合計トークン: 入力 {tin:,} / 出力 {tout:,} (概算 ${cost:.2f})"]
    (OUT / "レポート.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"レポート更新: {OUT / 'レポート.md'}  ({total}件, 成功{built})")


def main():
    if "--report" in sys.argv:
        make_report()
        return
    files = sorted(DXF_ROOT.rglob("*.dxf"))
    todo = [p for p in files if not (RESULTS / (slug(p) + ".json")).exists()]
    print(f"全{len(files)}件 / 未処理{len(todo)}件")
    start = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {}
        it = iter(todo)
        def submit_next():
            if time.time() - start > BUDGET_SEC:
                return False
            p = next(it, None)
            if p is None:
                return False
            futures[ex.submit(process, p)] = p
            return True
        for _ in range(WORKERS):
            submit_next()
        while futures:
            for fut in as_completed(list(futures)):
                p = futures.pop(fut)
                rec = fut.result()
                (RESULTS / (slug(p) + ".json")).write_text(
                    json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
                done += 1
                print(f"[{done}] {rec['status']:17s} {rec.get('seconds','?'):>6}s  {p.name}")
                submit_next()
                break
    remaining = len(todo) - done
    print(f"\n今回 {done} 件処理 / 残り {remaining} 件")
    make_report()
    if remaining > 0:
        print("RERUN_NEEDED")


if __name__ == "__main__":
    main()
