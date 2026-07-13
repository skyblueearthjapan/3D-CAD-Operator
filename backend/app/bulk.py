# -*- coding: utf-8 -*-
"""一括3D化ジョブ (バックグラウンド実行)。

フォルダ内のDXFを順次AI解釈→3D化し、進捗をポーリングで返す。
生成中も他の業務ができるよう、開始したら即座にジョブIDを返す設計。
出力は Documents\\3DCADオペレータ\\一括3D化\\<日時_フォルダ名>\\ に保存。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

MAX_FILES = 300
MAX_MB = 10

JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _safe(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>| ]", "_", name)[:80]


def start_job(files: list[Path], out_root: Path, label: str) -> dict:
    job_id = uuid.uuid4().hex[:10]
    out_dir = out_root / f"{datetime.now():%m%d_%H%M}_{_safe(label)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "label": label,
        "total": len(files),
        "done": 0,
        "running": True,
        "current": None,
        "out_dir": str(out_dir),
        "results": [],
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_worker, args=(job, files, out_dir), daemon=True).start()
    return job


def _worker(job: dict, files: list[Path], out_dir: Path):
    from build123d import export_gltf, export_step

    from .ai_interpreter import (SpecBuildError, build_from_spec, compare_specs,
                                 gemini_cross_check, generate_dump, interpret, verify)
    from .dxf_parser import DxfDocument

    for p in files:
        job["current"] = p.name
        rec: dict = {"name": p.name}
        t0 = time.time()
        try:
            if p.stat().st_size > MAX_MB * 1024 * 1024:
                rec["status"] = "skipped_large"
                raise StopIteration
            doc = DxfDocument(str(p))
            dump = generate_dump(doc)
            spec, usage = interpret(dump)
            rec["engine"] = usage.get("model")
            # 一括時は常にGeminiクロスレビュー (バックグラウンドなので遅延許容。
            # Geminiが解釈エンジンだった場合はスキップ)
            if (not rec["engine"].startswith("gemini")
                    and os.environ.get("GEMINI_API_KEY")):
                try:
                    diffs = compare_specs(spec, gemini_cross_check(dump))
                    if diffs:
                        rec["cross_check_diffs"] = diffs
                except Exception:
                    pass  # クロスレビュー失敗は本処理を止めない
            if usage.get("fallback_reason"):
                rec["fallback"] = usage["fallback_reason"][:120]
            rec["part_name"] = spec.part_name
            rec["shape_class"] = spec.shape_class
            rec["assumptions"] = spec.assumptions
            rec["drawing_conflicts"] = spec.drawing_conflicts
            if spec.unsupported_reason:
                rec["reason"] = spec.unsupported_reason
            try:
                solid = build_from_spec(spec)
                v = verify(solid, spec)
                base = _safe(p.stem)
                export_step(solid, str(out_dir / f"{base}_AI.step"))
                export_gltf(solid, str(out_dir / f"{base}_AI.glb"), binary=True)
                rec["verification"] = v
                rec["step"] = f"{base}_AI.step"
                rec["glb"] = f"{base}_AI.glb"
                rec["status"] = "built" if v["brep_valid"] else "built_invalid"
            except SpecBuildError as e:
                rec["status"] = "interpreted_only"
                rec["error"] = (spec.unsupported_reason or str(e))[:300]
            except Exception as e:
                rec["status"] = "build_failed"
                rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        except StopIteration:
            pass
        except Exception as e:
            rec["status"] = "error"
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        rec["seconds"] = round(time.time() - t0, 1)
        job["results"].append(rec)
        job["done"] += 1
        try:  # 進捗をディスクにも残す (アプリ再起動後も参照可能)
            (out_dir / "job.json").write_text(
                json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    job["running"] = False
    job["current"] = None
    try:
        (out_dir / "job.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def get_job(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def list_jobs() -> list[dict]:
    return [{k: j[k] for k in ("id", "label", "total", "done", "running", "started_at")}
            for j in JOBS.values()]
