# -*- coding: utf-8 -*-
"""Gemini主エンジンでの実地検証: 代表8枚 (全形状クラス) を解釈→ビルド→検証。"""
import io
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\3D-CAD-Operator\backend")

os.environ["AI_PRIMARY_ENGINE"] = "gemini"

from app.ai_interpreter import (SpecBuildError, build_from_spec, generate_dump,
                                interpret, verify)
from app.dxf_parser import DxfDocument

BASE = Path(r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3DCADオペレータ\DXFデータ 部品表用")
TARGETS = [
    ("circular", BASE / "POS(回転)" / "25152-3-05_ギアフタ.dxf"),
    ("circular", BASE / "POS(回転)" / "15062-3-003.dxf"),
    ("rect",     BASE / "POS(回転)" / "22129-P3-07_回転フレーム開口部カバー.dxf"),
    ("rect",     BASE / "SD" / "13063-S-019.dxf"),
    ("profile",  BASE / "SD" / "25152-S-11_ドグ.dxf"),
    ("revolved", BASE / "POS(昇降)" / "22129-P1-05_ベアリングケース.dxf"),
    ("revolved", BASE / "POS(回転)" / "15015-P3-012_013.dxf"),
    ("rect",     BASE / "POS(回転)" / "25052-3-07_減速機取付フランジ.dxf"),
]

ok = 0
for i, (cls, path) in enumerate(TARGETS):
    print("=" * 66)
    print(f"[{i+1}/8] {path.name} (期待クラス: {cls})")
    try:
        doc = DxfDocument(str(path))
        spec, usage = interpret(generate_dump(doc))
        fb = usage.get("fallback_reason")
        print(f"  engine={usage['model']}{' ※'+fb[:50] if fb else ''}  "
              f"class={spec.shape_class}  t={spec.thickness}  穴={len(spec.holes)}")
        try:
            solid = build_from_spec(spec)
            v = verify(solid, spec)
            mark = "✅" if v["brep_valid"] and not v["dimension_warnings"] else "⚠"
            print(f"  {mark} build vol={v['volume_mm3']:,.0f} bbox={v['bbox']} "
                  f"warn={v['dimension_warnings'] or 'なし'}")
            if v["brep_valid"]:
                ok += 1
        except SpecBuildError as e:
            print(f"  📋 解釈のみ: {str(e)[:100]}")
        except Exception as e:
            print(f"  ✗ build失敗: {e}")
    except Exception as e:
        print(f"  ✗ エラー: {str(e)[:150]}")
    time.sleep(12)  # 無料枠RPM対策

print(f"\nGemini主エンジン検証: {ok}/8 自動3D化成功")
