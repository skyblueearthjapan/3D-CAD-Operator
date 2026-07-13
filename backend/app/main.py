# -*- coding: utf-8 -*-
"""DXF → 3D CAD (STEP) 変換システム  API サーバ。

起動:  uvicorn app.main:app --reload  (backend ディレクトリで)
環境変数 DXF_ROOT: 社内 DXF フォルダのルート (フォルダブラウズ機能)
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
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


def _parse_and_respond(filepath: str, display_name: str) -> dict:
    try:
        doc = DxfDocument(filepath)
    except Exception as e:
        raise HTTPException(422, f"DXF の解析に失敗しました: {e}")
    sid = _new_session(display_name, doc)
    data = doc.to_json()
    return {"session": sid, "name": display_name, **data}


@app.post("/api/open")
def open_from_root(req: OpenReq):
    """サーバフォルダ内の DXF を開く。"""
    p = (DXF_ROOT / req.path).resolve()
    if not str(p).startswith(str(DXF_ROOT.resolve())) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    return _parse_and_respond(str(p), p.name)


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
