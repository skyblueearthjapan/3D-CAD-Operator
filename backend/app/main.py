# -*- coding: utf-8 -*-
"""DXF → 3D CAD (STEP) 変換システム  API サーバ。

起動:  uvicorn app.main:app --reload  (backend ディレクトリで)
環境変数 DXF_ROOT: 社内 DXF フォルダのルート (フォルダブラウズ機能)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .contours import Loop, detect_loops
from .dxf_parser import DxfDocument
from .modeler import ModelError, build_solid, export_outputs

DXF_ROOT = Path(os.environ.get(
    "DXF_ROOT",
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\DXFデータ 部品表用",
))
WORK_DIR = Path(tempfile.gettempdir()) / "dxf2step_sessions"
WORK_DIR.mkdir(exist_ok=True)

app = FastAPI(title="DXF → STEP 変換システム")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# セッション: {id: {"doc": DxfDocument, "loops": {loop_id: Loop}, "dir": Path, "name": str}}
SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 30


def _new_session(name: str, doc: DxfDocument) -> str:
    while len(SESSIONS) >= MAX_SESSIONS:
        oldest = next(iter(SESSIONS))
        shutil.rmtree(SESSIONS[oldest]["dir"], ignore_errors=True)
        del SESSIONS[oldest]
    sid = uuid.uuid4().hex[:12]
    sdir = WORK_DIR / sid
    sdir.mkdir(exist_ok=True)
    SESSIONS[sid] = {"doc": doc, "loops": {}, "dir": sdir, "name": name}
    return sid


def _get_session(sid: str) -> dict:
    if sid not in SESSIONS:
        raise HTTPException(404, "セッションが見つかりません。ファイルを開き直してください。")
    return SESSIONS[sid]


# ------------------------------------------------------------------ ファイル

@app.get("/api/browse")
def browse(path: str = ""):
    """DXF_ROOT 配下のフォルダ/DXF 一覧。"""
    base = (DXF_ROOT / path).resolve()
    if not str(base).startswith(str(DXF_ROOT.resolve())):
        raise HTTPException(400, "不正なパスです")
    if not base.exists():
        return {"root": str(DXF_ROOT), "dirs": [], "files": [], "exists": False}
    dirs, files = [], []
    for p in sorted(base.iterdir()):
        rel = str(p.relative_to(DXF_ROOT))
        if p.is_dir():
            dirs.append({"name": p.name, "path": rel})
        elif p.suffix.lower() == ".dxf":
            files.append({"name": p.name, "path": rel, "size": p.stat().st_size})
    return {"root": str(DXF_ROOT), "dirs": dirs, "files": files, "exists": True}


class OpenReq(BaseModel):
    path: str


def _parse_and_respond(filepath: str, display_name: str,
                       relpath: str | None = None) -> dict:
    try:
        doc = DxfDocument(filepath)
    except Exception as e:
        raise HTTPException(422, f"DXF の解析に失敗しました: {e}")
    sid = _new_session(display_name, doc)
    SESSIONS[sid]["relpath"] = relpath
    data = doc.to_json()
    return {"session": sid, "name": display_name, **data}


@app.post("/api/open")
def open_from_root(req: OpenReq):
    """サーバフォルダ内の DXF を開く。"""
    p = (DXF_ROOT / req.path).resolve()
    if not str(p).startswith(str(DXF_ROOT.resolve())) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    return _parse_and_respond(str(p), p.name, relpath=req.path)


@app.post("/api/upload")
async def upload(file: UploadFile):
    """ドラッグ&ドロップされた DXF を開く。"""
    if not (file.filename or "").lower().endswith(".dxf"):
        raise HTTPException(400, "DXF ファイルを指定してください")
    tmp = WORK_DIR / f"up_{uuid.uuid4().hex[:8]}.dxf"
    with tmp.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        return _parse_and_respond(str(tmp), file.filename)
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ 輪郭検出

class ContourReq(BaseModel):
    session: str
    layers: list[str]


@app.post("/api/contours")
def contours(req: ContourReq):
    s = _get_session(req.session)
    loops = detect_loops(s["doc"].segments, set(req.layers) if req.layers else None)
    s["loops"] = {l.id: l for l in loops}
    return {"loops": [l.to_json() for l in loops]}


# ------------------------------------------------------------------ 3D 生成

class ModelReq(BaseModel):
    session: str
    outer: int
    holes: list[int] = []
    thickness: float
    mode: str = "up"          # up | down | mid


@app.post("/api/model")
def model(req: ModelReq):
    s = _get_session(req.session)
    loops: dict[int, Loop] = s["loops"]
    if req.outer not in loops:
        raise HTTPException(400, "外形輪郭を選択してください")
    holes = [loops[h] for h in req.holes if h in loops]
    try:
        solid = build_solid(loops[req.outer], holes, req.thickness, req.mode)
        base = Path(s["name"]).stem or "model"
        step_path = s["dir"] / f"{base}.step"
        glb_path = s["dir"] / f"{base}.glb"
        stats = export_outputs(solid, str(step_path), str(glb_path))
    except ModelError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"モデリングエラー: {e}")
    return {
        "step": f"/api/file/{req.session}/{step_path.name}",
        "glb": f"/api/file/{req.session}/{glb_path.name}",
        **stats,
    }


# ------------------------------------------------------------------ AI 解釈

class AiInterpretReq(BaseModel):
    session: str
    cross_check: bool = False   # Gemini第二意見 (GEMINI_API_KEY 設定時のみ有効)
    region: list[float] | None = None  # [x0,y0,x1,y1] 図面座標。指定時は範囲内のみ解釈


# 生成結果の永続保存: DXFごとに最後の生成結果を保持し、開き直したとき復元する
CACHE_ROOT = DXF_ROOT.parent / "生成済み3D"


def _cache_slug(relpath: str) -> str:
    return re.sub(r"[\\/:*?\"<>| ]", "_", relpath)[:120]


def _save_to_cache(relpath: str, result: dict, session_dir: Path, base: str):
    cdir = CACHE_ROOT / _cache_slug(relpath)
    cdir.mkdir(parents=True, exist_ok=True)
    cached = dict(result)
    cached["cached_at"] = datetime.now().isoformat(timespec="seconds")
    cached["source_path"] = relpath
    slug = _cache_slug(relpath)
    for key, suffix in (("step", ".step"), ("glb", ".glb")):
        src = session_dir / f"{base}_AI{suffix}"
        if result.get(key) and src.exists():
            shutil.copy2(src, cdir / src.name)
            cached[key] = f"/api/cached/{slug}/{src.name}"
    (cdir / "result.json").write_text(
        json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/cached_result")
def cached_result(path: str):
    """保存済みの生成結果 (あれば)。フロントは図面を開いたときにこれで3Dを復元する。"""
    f = CACHE_ROOT / _cache_slug(path) / "result.json"
    if not f.exists():
        return {"exists": False}
    try:
        return {"exists": True, "result": json.loads(f.read_text(encoding="utf-8"))}
    except Exception:
        return {"exists": False}


@app.get("/api/cached/{slug}/{name}")
def cached_file(slug: str, name: str):
    p = (CACHE_ROOT / slug / name).resolve()
    if not str(p).startswith(str(CACHE_ROOT.resolve())) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    media = "model/gltf-binary" if p.suffix == ".glb" else "application/step"
    return FileResponse(str(p), media_type=media, filename=name)


@app.post("/api/ai_interpret")
def ai_interpret(req: AiInterpretReq):
    """AI図面解釈 (Claude優先・Geminiフォールバック) → 形状仕様JSON → (対応形状なら) 自動3D化。"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        raise HTTPException(503, "ANTHROPIC_API_KEY / GEMINI_API_KEY のいずれも設定されていません")
    s = _get_session(req.session)
    from .ai_interpreter import run_interpret
    try:
        base = Path(s["name"]).stem or "model"
        result = run_interpret(s["doc"], s["dir"], base, req.session,
                               cross_check=req.cross_check, region=req.region)
    except Exception as e:
        raise HTTPException(500, f"AI解釈エラー: {e}")
    if s.get("relpath"):
        try:  # 保存失敗は本処理を止めない
            _save_to_cache(s["relpath"], result, s["dir"], base)
        except Exception:
            pass
    return result


# ------------------------------------------------------------------ 一括3D化

BULK_ROOT = DXF_ROOT.parent / "一括3D化"


class BulkStartReq(BaseModel):
    path: str = ""          # DXF_ROOT からの相対フォルダ
    recursive: bool = True
    files: list[str] | None = None  # 指定時: このファイルリスト(相対パス)のみ処理


@app.post("/api/bulk_start")
def bulk_start(req: BulkStartReq):
    """一括3D化をバックグラウンドで開始し、ジョブIDを即返す。

    files指定時はチェックされたファイルのみ、未指定時はフォルダ内全DXFを対象にする。
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        raise HTTPException(503, "APIキーが設定されていません")
    from .bulk import MAX_FILES, start_job
    if req.files:
        files = []
        for rel in req.files:
            p = (DXF_ROOT / rel).resolve()
            if not str(p).startswith(str(DXF_ROOT.resolve())) or not p.exists():
                raise HTTPException(404, f"ファイルが見つかりません: {rel}")
            if p.suffix.lower() == ".dxf":
                files.append(p)
        files = sorted(set(files))
        label = f"選択{len(files)}件"
    else:
        base = (DXF_ROOT / req.path).resolve()
        if not str(base).startswith(str(DXF_ROOT.resolve())) or not base.exists():
            raise HTTPException(404, "フォルダが見つかりません")
        files = sorted(base.rglob("*.dxf") if req.recursive else base.glob("*.dxf"))
        label = req.path or "部品表フォルダ全体"
    if not files:
        raise HTTPException(400, "対象の DXF がありません")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"対象が多すぎます ({len(files)}件 > 上限{MAX_FILES}件)")
    job = start_job(files, BULK_ROOT, label)
    return {"job_id": job["id"], "total": job["total"], "out_dir": job["out_dir"]}


@app.get("/api/bulk/{job_id}")
def bulk_status(job_id: str):
    from .bulk import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "ジョブが見つかりません")
    return job


@app.get("/api/bulk/{job_id}/file/{name}")
def bulk_file(job_id: str, name: str):
    from .bulk import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "ジョブが見つかりません")
    p = (Path(job["out_dir"]) / name).resolve()
    if not str(p).startswith(str(Path(job["out_dir"]).resolve())) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    media = "model/gltf-binary" if p.suffix == ".glb" else "application/step"
    return FileResponse(str(p), media_type=media, filename=name)


@app.get("/api/file/{sid}/{name}")
def get_file(sid: str, name: str):
    s = _get_session(sid)
    p = (s["dir"] / name).resolve()
    if not str(p).startswith(str(s["dir"].resolve())) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    media = "model/gltf-binary" if p.suffix == ".glb" else "application/step"
    return FileResponse(str(p), media_type=media, filename=name)


# ------------------------------------------------------------------ 静的配信

_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
