# -*- coding: utf-8 -*-
"""AI解釈エンジンのE2Eテスト (APIサーバ不要・直接呼び出し)。

使い方:  backend ディレクトリで
    python test_ai_interpret.py                # 既定2枚(ギアフタ・ドグ)
    python test_ai_interpret.py <dxfパス> ...  # 任意の図面
環境変数 ANTHROPIC_API_KEY 必須。GEMINI_API_KEY があればクロスチェックも実行。
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ai_interpreter import run_interpret
from app.dxf_parser import DxfDocument

BASE = Path(os.environ.get(
    "DXF_ROOT",
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\DXFデータ 部品表用"))

DEFAULTS = [
    BASE / "POS(回転)" / "25152-3-05_ギアフタ.dxf",
    BASE / "SD" / "25152-S-11_ドグ.dxf",
]

targets = [Path(p) for p in sys.argv[1:]] or DEFAULTS
out_dir = Path(tempfile.gettempdir()) / "ai_interpret_test"
out_dir.mkdir(exist_ok=True)

for path in targets:
    print("=" * 70)
    print(path.name)
    doc = DxfDocument(str(path))
    result = run_interpret(doc, out_dir, path.stem, "test",
                           cross_check=bool(os.environ.get("GEMINI_API_KEY")))
    spec = result["spec"]
    u = result["usage"]
    print(f"  解釈エンジン: {u['model']}"
          + (f"  ※{u['fallback_reason'][:60]}" if u.get("fallback_reason") else ""))
    print(f"  部品名: {spec['part_name']}  /  形状クラス: {spec['shape_class']}")
    print(f"  材質: {spec['material']}")
    print(f"  板厚: {spec.get('thickness')}  外径: {spec.get('outer_diameter')}  "
          f"L×W: {spec.get('length')}×{spec.get('width')}")
    print(f"  穴: {len(spec['holes'])}件")
    for h in spec["holes"][:14]:
        print(f"    ({h['x']:.1f},{h['y']:.1f}) φ{h.get('diameter')} thread={h.get('thread')} "
              f"csk={h.get('csk_diameter')} cbore={h.get('cbore_diameter')}x{h.get('cbore_depth')}")
    if spec["assumptions"]:
        print("  仮定:", *[f"\n    - {a}" for a in spec["assumptions"]])
    if spec["drawing_conflicts"]:
        print("  図面矛盾:", *[f"\n    - {c}" for c in spec["drawing_conflicts"]])
    if result["buildable"]:
        v = result["verification"]
        print(f"  ビルド: OK  BRepCheck={v['brep_valid']}  vol={v['volume_mm3']:,.0f}mm3  "
              f"bbox={v['bbox']}  警告={v['dimension_warnings'] or 'なし'}")
        print(f"  出力: {out_dir / (path.stem + '_AI.step')}")
    else:
        print(f"  ビルド: 不可 → {result['build_error']}")
    if result["cross_check_diffs"]:
        print("  ★Gemini不一致:", result["cross_check_diffs"])
    print(f"  トークン: in={result['usage']['input_tokens']} out={result['usage']['output_tokens']}")

print("\ndone")
