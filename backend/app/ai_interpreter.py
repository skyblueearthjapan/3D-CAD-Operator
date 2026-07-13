# -*- coding: utf-8 -*-
"""AI 図面解釈エンジン (Claude Opus 4.8)。

フロー:
  1. generate_dump()   : DXF をエンティティダンプ (テキスト) に変換
  2. interpret()       : Claude が形状仕様 (ShapeSpec) を構造化出力で返す
  3. build_from_spec() : 対応形状クラスなら build123d でソリッド化
  4. verify()          : BRepCheck + 寸法照合
  5. gemini_cross_check(): (任意) Gemini による第二意見

必要な環境変数: ANTHROPIC_API_KEY (必須), GEMINI_API_KEY (クロスチェック時のみ)
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ 形状仕様

# 解釈に使うモデル (環境変数で切替。Sonnet=低コスト / Opus=難部品に強い)
AI_MODEL = os.environ.get("AI_INTERPRET_MODEL", "claude-sonnet-5")
# 主エンジン: "claude" (既定) | "gemini" (低コスト運用。もう片方がフォールバック)
AI_PRIMARY = os.environ.get("AI_PRIMARY_ENGINE", "claude")
# 思考量: 未設定=モデル既定(high)。"medium"/"low" で出力トークン(コスト)を削減
AI_EFFORT = os.environ.get("AI_EFFORT")

# タップ→下穴径 (JIS 並目 / 管用テーパPT)
TAP_DRILL = {"M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8,
             "M10": 8.5, "M12": 10.2, "M14": 12.0, "M16": 14.0,
             "M18": 15.5, "M20": 17.5, "M24": 21.0,
             "PT1/8": 8.4, "PT1/4": 11.0, "PT3/8": 14.5, "PT1/2": 18.0}


class HoleSpec(BaseModel):
    """1つの穴 (同一仕様が複数あれば1件ずつ列挙する)。"""
    x: float = Field(description="穴中心X。circular/revolvedは中心軸原点、rect/profileは外形左下原点")
    y: float = Field(description="穴中心Y (同上の座標系)")
    diameter: Optional[float] = Field(None, description="キリ穴径。タップのみなら null 可")
    thread: Optional[str] = Field(None, description="ねじ呼び 'M6' 等 (タップ穴の場合)")
    csk_diameter: Optional[float] = Field(None, description="皿ザグリの表面径 (90°皿)")
    cbore_diameter: Optional[float] = Field(None, description="ザグリ径")
    cbore_depth: Optional[float] = Field(None, description="ザグリ深さ")
    through: bool = Field(True, description="貫通か")
    depth: Optional[float] = Field(None, description="止まり穴の深さ (through=falseのとき)")
    from_face: Literal["top", "bottom"] = Field("top", description="加工面 (皿/ザグリのある面)")
    axis: Literal["z", "y"] = Field(
        "z", description="穴軸: z=板厚方向(既定)。y=profile_plate専用、プロファイル上端(小口)から"
                         "下向きの端面ドリル (xのみ使用、yは0でよい)")
    note: Optional[str] = Field(None, description="元注記 (例 '12-14キリ ザグリ20深13 PCD378')")


class StackStep(BaseModel):
    """回転体の段 (直径×高さ)。"""
    diameter: float
    height: float


class ShapeSpec(BaseModel):
    """図面から解釈した 3D 形状仕様。"""
    part_name: str = Field(description="部品名")
    material: Optional[str] = Field(None, description="材質・熱処理")
    shape_class: Literal["circular_plate", "rect_plate", "profile_plate",
                         "revolved", "unsupported"] = Field(
        description="circular_plate=円板+穴 / rect_plate=矩形板+穴 / "
                    "profile_plate=任意多角形輪郭の板+穴 / revolved=旋盤物(段付き円筒) / "
                    "unsupported=それ以外(歯車・曲げ板金・複合形状など)")
    thickness: Optional[float] = Field(None, description="板厚 (plate系で必須)")
    outer_diameter: Optional[float] = Field(None, description="外径 (circular_plateで必須)")
    length: Optional[float] = Field(None, description="長さX (rect_plateで必須)")
    width: Optional[float] = Field(None, description="幅Y (rect_plateで必須)")
    profile_points: Optional[list[list[float]]] = Field(
        None, description="profile_plate の閉多角形頂点 [[x,y],...] (左下原点・CCW)")
    outer_stack: Optional[list[StackStep]] = Field(
        None, description="revolved の外形段 (底面z=0から上へ順)")
    bore_stack: Optional[list[StackStep]] = Field(
        None, description="revolved の内径段 (上面から下へ順。貫通なら合計高さ=全長)")
    holes: list[HoleSpec] = Field(default_factory=list)
    chamfer_notes: list[str] = Field(default_factory=list, description="面取り・R指示 (モデル化は任意)")
    unmodeled_features: list[str] = Field(
        default_factory=list, description="仕様に載せられなかった特徴 (半径方向タップ・キー溝・歯車諸元など)")
    unsupported_reason: Optional[str] = Field(
        None, description="shape_class='unsupported' の場合は必須: なぜ自動3D化できないか"
                          "(部品の種類、どの特徴が対応外か、人が3D化する際のポイント)を日本語1〜3文で")
    assumptions: list[str] = Field(default_factory=list, description="図面から確定できず仮定した事項")
    drawing_conflicts: list[str] = Field(
        default_factory=list, description="図面内の矛盾 (作図と注記の食い違い等) と採用した解釈")


RULES = """あなたは機械製図の専門家です。日本の機械部品図(JIS・第三角法)のDXFエンティティダンプを読み、部品の3D形状仕様を返してください。

凡例:
- linetype CONTINUOUS=実線(可視輪郭)、DASHDOT=一点鎖線(中心線・PCD円)、HIDDEN=破線(隠れ線)
- レイヤ 254/255/0/LEVEL254 などは図枠・表題欄・部品表であることが多い(形状ではない)
- 風船(注記用の小円+LEADER)は形状に含めない
- NOTES の日本語注記が重要: 材質、ＰＬ○=板厚、○キリ=ドリル穴、ザグリ=counterbore、皿=countersink(90°)、リーマ、Ｍ○=ねじ、ＰＣＤ○=ピッチ円直径、Ｃ○=面取り、ｍ○-○Ｔ=歯車(モジュール-歯数)、マル○=丸棒素材径
- DIMENSION の text が空でない場合(例 '1830')は実寸の上書き=破断図の可能性が高い。座標は長さ方向に縮み、断面方向は1:1
- 複数ビューを突き合わせ、穴の向き・深さ・皿/ザグリの面を判断すること
- 材質のPL値は素材厚。断面図に仕上げ厚の寸法があればそちらを優先すること
- 作図と注記が食い違う場合(例: 円がφ7で注記が2-φ8)は注記を優先し drawing_conflicts に記録
- PCD上の等配穴は1穴ずつ座標展開して holes に列挙する(count表現ではない)
- タップは thread に呼びを入れる(diameter は null でよい。下穴はビルダー側で決める)

座標系の約束:
- circular_plate / revolved: 原点=中心軸、板/段の底面が z=0
- rect_plate / profile_plate: 原点=外形の左下隅
- 穴の from_face は皿・ザグリがある側。図面から不明なら top とし assumptions に記録
- profile_plate の穴軸: プロファイル面(板面)に垂直なら axis='z'(既定)。
  平面図(プロファイルを上から見たビュー、幅=板厚の細長い矩形)に穴の円が描かれている場合、
  その穴はプロファイル上端(小口)から下向きのドリルなので axis='y' とし x=長手位置、y=0 とする

shape_class の選び方:
- 平板(円/矩形/多角形)+板厚方向の穴 → 各 plate クラス
- 旋盤物(同軸の段付き円筒、フランジ含む) → revolved (外形=outer_stack、内径=bore_stack、フランジのボルト穴は holes)
- 歯車・曲げ板金・カム輪郭以外の複合形状・半径方向穴が主体 → unsupported (その場合も判る限り各フィールドを埋め、unmodeled_features に詳細を書き、
  unsupported_reason に「なぜ自動3D化できないか・人が3D化する際のポイント」を日本語で必ず書く。
  組立図・購入品・ステッカー等そもそも単品加工部品でないものはその旨を明記)

必ず ShapeSpec スキーマに従うこと。寸法は図面のDIMENSION・注記・座標整合から決定し、推測値は assumptions に明記。"""


# ------------------------------------------------------------------ ダンプ生成

def generate_dump(dxfdoc) -> str:
    """DxfDocument (ezdxf doc/msp保持) からAI入力用エンティティダンプを生成。"""
    msp = dxfdoc.msp
    out: list[str] = []
    w = out.append

    cnt = Counter((e.dxftype(), e.dxf.layer, e.dxf.get("linetype", "BYLAYER")) for e in msp)
    w("-- counts (type, layer, linetype):")
    for (t, layer, lt), n in sorted(cnt.items(), key=lambda x: -x[1]):
        w(f"   {t:12s} L={layer:10s} lt={lt:12s} x{n}")

    circles = list(msp.query("CIRCLE"))
    w(f"-- CIRCLE ({len(circles)}):")
    for e in circles[:250]:
        c = e.dxf.center
        w(f"   ({c.x:9.2f},{c.y:9.2f}) R={e.dxf.radius:8.3f} lt={e.dxf.get('linetype','BYLAYER')} L={e.dxf.layer}")
    if len(circles) > 250:
        w(f"   ... (残り{len(circles) - 250}件省略)")

    arcs = list(msp.query("ARC"))
    w(f"-- ARC ({len(arcs)}):")
    for e in arcs[:80]:
        c = e.dxf.center
        w(f"   ({c.x:9.2f},{c.y:9.2f}) R={e.dxf.radius:8.3f} "
          f"a={e.dxf.start_angle:.1f}..{e.dxf.end_angle:.1f} lt={e.dxf.get('linetype','BYLAYER')} L={e.dxf.layer}")

    w("-- LWPOLYLINE:")
    for e in list(msp.query("LWPOLYLINE"))[:120]:
        pts = [(round(p[0], 2), round(p[1], 2)) for p in e.get_points()]
        head = f"   n={len(pts)} closed={e.closed} lt={e.dxf.get('linetype','BYLAYER')} L={e.dxf.layer} "
        if len(pts) <= 16:
            w(head + f"pts={pts}")
        else:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            w(head + f"bbox=({min(xs):.1f},{min(ys):.1f})-({max(xs):.1f},{max(ys):.1f})")

    lines = list(msp.query("LINE"))
    w(f"-- LINE ({len(lines)}):")
    for e in lines[:300]:
        s, t = e.dxf.start, e.dxf.end
        w(f"   ({s.x:8.2f},{s.y:8.2f})-({t.x:8.2f},{t.y:8.2f}) lt={e.dxf.get('linetype','BYLAYER')} L={e.dxf.layer}")

    w("-- NOTES:")
    note_count = 0
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT"):
            raw = e.dxf.text if e.dxftype() == "TEXT" else e.text
            clean = re.sub(r"\\[A-Za-z][0-9.]*;?|[{}]|\\P", "|", raw or "").strip("| ")
            if clean:
                w(f"   {clean[:100]}")
                note_count += 1
                if note_count >= 250:
                    w("   ... (以降の注記省略)")
                    break

    w("-- DIMENSION:")
    for e in list(msp.query("DIMENSION"))[:150]:
        try:
            m = round(e.get_measurement(), 3)
        except Exception:
            m = "?"
        d = e.dxf
        try:
            tm = d.text_midpoint
            at = f"at({tm.x:.0f},{tm.y:.0f})"
        except Exception:
            at = ""
        txt = re.sub(r"\\A\d;", "", d.text or "")
        w(f"   dimtype={e.dimtype} m={m} text='{txt}' {at}")
    return "\n".join(out)


# ------------------------------------------------------------------ Claude 解釈

def _extract_json(text: str) -> dict:
    """応答テキストから JSON を取り出す (コードフェンス・前置きに耐性)。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    return json.loads(text)


def interpret(dump: str, force: str | None = None) -> tuple[ShapeSpec, dict]:
    """図面ダンプを ShapeSpec に解釈する。AI_PRIMARY_ENGINE の主エンジン優先、失敗時はもう片方へ。

    force="claude"|"gemini" で特定エンジンのみ使用 (品質再解釈用)。
    """
    engines = [("claude", _interpret_claude, "ANTHROPIC_API_KEY"),
               ("gemini", _interpret_gemini, "GEMINI_API_KEY")]
    if AI_PRIMARY == "gemini":
        engines.reverse()
    if force:
        engines = [e for e in engines if e[0] == force]
    first_err: Exception | None = None
    for i, (name, fn, key) in enumerate(engines):
        if not os.environ.get(key):
            continue
        try:
            spec, usage = fn(dump)
            if i > 0 or first_err:
                usage["fallback_reason"] = (
                    f"{engines[0][0]}失敗のため{name}で解釈: {str(first_err)[:200]}"
                    if first_err else f"{engines[0][0]}のキー未設定のため{name}で解釈")
            return spec, usage
        except Exception as e:
            if first_err is None:
                first_err = e
    raise RuntimeError(f"全エンジンで解釈に失敗しました: {first_err}")


def _interpret_claude(dump: str) -> tuple[ShapeSpec, dict]:
    """Claude で解釈。スキーマはプロンプト埋め込み + Pydantic 検証 + 1リトライ。"""
    import anthropic
    client = anthropic.Anthropic()
    schema = json.dumps(ShapeSpec.model_json_schema(), ensure_ascii=False)
    system = [{"type": "text",
               "text": RULES + "\n\n出力は次のJSONスキーマに厳密に従うJSONオブジェクトのみ"
                               "(前置き・コードフェンス不要):\n" + schema,
               "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": dump}]
    total_in = total_out = 0
    last_err: Exception | None = None
    for _ in range(2):  # 初回 + 検証エラー時1回リトライ
        extra = {"output_config": {"effort": AI_EFFORT}} if AI_EFFORT else {}
        resp = client.messages.create(
            model=AI_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            messages=messages,
            **extra,
        )
        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude が応答を拒否しました (安全性判定)")
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            spec = ShapeSpec.model_validate(_extract_json(text))
            usage = {"input_tokens": total_in, "output_tokens": total_out,
                     "model": resp.model}
            return spec, usage
        except Exception as e:
            last_err = e
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": f"JSONがスキーマ検証に失敗しました: {e}\n"
                            "修正した完全なJSONのみを再出力してください。"},
            ]
    raise RuntimeError(f"ShapeSpec の解析に失敗しました: {last_err}")


def _interpret_gemini(dump: str) -> tuple[ShapeSpec, dict]:
    """Gemini で解釈 (フォールバック用)。同一ルール + Pydantic 検証 + 1リトライ。"""
    from google import genai
    from google.genai import types
    model = os.environ.get("GEMINI_INTERPRET_MODEL", "gemini-2.5-pro")
    client = genai.Client()
    schema = json.dumps(ShapeSpec.model_json_schema(), ensure_ascii=False)
    system = (RULES + "\n\nすべての文字列値(部品名・仮定・矛盾など)は必ず日本語で書くこと。"
                      "\n出力は次のJSONスキーマに厳密に従うJSONオブジェクトのみ"
                      "(前置き・コードフェンス不要):\n" + schema)
    contents: list = [dump]
    total_in = total_out = 0
    last_err: Exception | None = None
    for _ in range(2):
        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
            ),
        )
        u = resp.usage_metadata
        total_in += u.prompt_token_count or 0
        total_out += (u.candidates_token_count or 0)
        try:
            spec = ShapeSpec.model_validate(_extract_json(resp.text or ""))
            return spec, {"input_tokens": total_in, "output_tokens": total_out,
                          "model": model}
        except Exception as e:
            last_err = e
            contents = contents + [
                resp.text or "",
                f"JSONがスキーマ検証に失敗しました: {e}\n修正した完全なJSONのみを再出力してください。",
            ]
    raise RuntimeError(f"Geminiフォールバックも解析失敗: {last_err}")


def gemini_cross_check(dump: str) -> dict | None:
    """Gemini による第二意見 (GEMINI_API_KEY があれば)。生JSONを返す。"""
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client()
        resp = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=dump,
            config=types.GenerateContentConfig(
                system_instruction=RULES + "\n\n出力はShapeSpec相当のJSONのみ。",
                response_mime_type="application/json",
            ),
        )
        return json.loads(resp.text)
    except Exception as e:
        return {"error": f"Gemini cross-check failed: {e}"}


def compare_specs(spec: ShapeSpec, gemini: dict | None) -> list[str]:
    """Claude と Gemini の主要値を機械比較し、不一致を列挙する。"""
    if not gemini or "error" in gemini:
        return []
    diffs: list[str] = []

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    g_t = num(gemini.get("thickness"))
    if spec.thickness and g_t and abs(spec.thickness - g_t) > 0.05:
        diffs.append(f"板厚: Claude={spec.thickness} / Gemini={g_t}")
    g_od = num(gemini.get("outer_diameter"))
    if spec.outer_diameter and g_od and abs(spec.outer_diameter - g_od) > 0.1:
        diffs.append(f"外径: Claude={spec.outer_diameter} / Gemini={g_od}")
    g_holes = gemini.get("holes")
    if isinstance(g_holes, list) and len(g_holes) != len(spec.holes):
        diffs.append(f"穴数: Claude={len(spec.holes)} / Gemini={len(g_holes)}")
    return diffs


# ------------------------------------------------------------------ ビルダー

class SpecBuildError(Exception):
    pass


def _hole_drill_diameter(h: HoleSpec) -> float:
    if h.diameter:
        return h.diameter
    if h.thread:
        key = h.thread.upper().replace("Ｍ", "M").split("X")[0].split("×")[0].strip()
        if key in TAP_DRILL:
            return TAP_DRILL[key]
    raise SpecBuildError(f"穴径を決定できません: {h}")


def build_from_spec(spec: ShapeSpec):
    """ShapeSpec からソリッドを生成。unsupported は SpecBuildError。"""
    from build123d import (Align, Box, BuildPart, BuildSketch, CounterBoreHole,
                           CounterSinkHole, Cylinder, Hole, Location, Locations,
                           Mode, Plane, Polygon, Pos, Rot, extrude)

    CCC = (Align.CENTER, Align.CENTER, Align.MIN)

    def apply_holes(p, top_z: float, y_range: tuple[float, float] | None = None):
        for h in spec.holes:
            d = _hole_drill_diameter(h)
            if h.axis == "y":
                # 端面ドリル (profile_plate 専用): 上端 y_max から -Y 方向
                if y_range is None:
                    raise SpecBuildError("axis='y' の穴は profile_plate でのみ使用できます")
                y_min, y_max = y_range
                length = (y_max - y_min + 1.0) if h.through else ((h.depth or 10.0) + 0.5)
                cy = y_max + 0.5 - length / 2
                with Locations(Pos(h.x, cy, top_z / 2) * Rot(90, 0, 0)):
                    Cylinder(d / 2, length, mode=Mode.SUBTRACT)
                continue
            # top: 上面から-Z方向 / bottom: 下面から+Z方向 (180°反転)
            loc = (Pos(h.x, h.y, top_z) if h.from_face == "top"
                   else Pos(h.x, h.y, 0) * Rot(180, 0, 0))
            with Locations(loc):
                if h.csk_diameter:
                    CounterSinkHole(radius=d / 2, counter_sink_radius=h.csk_diameter / 2,
                                    counter_sink_angle=90)
                elif h.cbore_diameter:
                    CounterBoreHole(radius=d / 2, counter_bore_radius=h.cbore_diameter / 2,
                                    counter_bore_depth=h.cbore_depth or 0.0)
                else:
                    Hole(radius=d / 2,
                         depth=None if h.through else (h.depth or None))

    if spec.shape_class == "circular_plate":
        if not (spec.outer_diameter and spec.thickness):
            raise SpecBuildError("circular_plate には outer_diameter と thickness が必要です")
        with BuildPart() as p:
            Cylinder(spec.outer_diameter / 2, spec.thickness, align=CCC)
            apply_holes(p, spec.thickness)
        return p.part

    if spec.shape_class == "rect_plate":
        if not (spec.length and spec.width and spec.thickness):
            raise SpecBuildError("rect_plate には length/width/thickness が必要です")
        with BuildPart() as p:
            Box(spec.length, spec.width, spec.thickness,
                align=(Align.MIN, Align.MIN, Align.MIN))
            apply_holes(p, spec.thickness)
        return p.part

    if spec.shape_class == "profile_plate":
        if not (spec.profile_points and spec.thickness):
            raise SpecBuildError("profile_plate には profile_points と thickness が必要です")
        ys = [pt[1] for pt in spec.profile_points]
        with BuildPart() as p:
            with BuildSketch(Plane.XY):
                Polygon(*[tuple(pt) for pt in spec.profile_points], align=None)
            extrude(amount=spec.thickness)
            apply_holes(p, spec.thickness, y_range=(min(ys), max(ys)))
        return p.part

    if spec.shape_class == "revolved":
        if not spec.outer_stack:
            raise SpecBuildError("revolved には outer_stack が必要です")
        total_h = sum(s.height for s in spec.outer_stack)
        with BuildPart() as p:
            z = 0.0
            for s in spec.outer_stack:
                with Locations(Location((0, 0, z))):
                    Cylinder(s.diameter / 2, s.height, align=CCC)
                z += s.height
            if spec.bore_stack:
                z_hi = total_h
                for s in spec.bore_stack:
                    z_lo = z_hi - s.height
                    # 上端が上面/下端が底面に一致する段は 0.5mm はみ出して確実に貫通させる
                    ext_top = 0.5 if abs(z_hi - total_h) < 1e-9 else 0.0
                    ext_bot = 0.5 if z_lo <= 1e-9 else 0.0
                    with Locations(Location((0, 0, z_lo - ext_bot))):
                        Cylinder(s.diameter / 2, s.height + ext_top + ext_bot,
                                 align=CCC, mode=Mode.SUBTRACT)
                    z_hi = z_lo
            apply_holes(p, total_h)
        return p.part

    raise SpecBuildError(
        "この形状クラスは自動ビルド未対応です: " + spec.shape_class +
        (" / 未対応特徴: " + "; ".join(spec.unmodeled_features) if spec.unmodeled_features else ""))


def verify(solid, spec: ShapeSpec) -> dict:
    """BRepCheck + バウンディングボックス照合 + 健全性チェック。"""
    from OCP.BRepCheck import BRepCheck_Analyzer
    ok = BRepCheck_Analyzer(solid.wrapped).IsValid()
    bb = solid.bounding_box()
    checks: list[str] = []
    def close(a, b, tol=0.5):
        return a is not None and abs(a - b) <= tol
    # 健全性: 極端に小さいソリッドは解釈失敗の可能性が高い
    if solid.volume < 100:
        checks.append(f"体積が極端に小さい ({solid.volume:.0f}mm3) — 解釈失敗の可能性")
    if spec.shape_class == "profile_plate" and spec.profile_points:
        xs = [p[0] for p in spec.profile_points]
        ys = [p[1] for p in spec.profile_points]
        if spec.thickness and max(max(xs) - min(xs), max(ys) - min(ys)) < spec.thickness:
            checks.append("プロファイル外形が板厚より小さい — 座標解釈の失敗の可能性")
    # 穴が外形の外にはみ出していないか (座標解釈ミスの典型症状)
    for h in spec.holes:
        r = (h.diameter or 0) / 2
        outside = False
        if spec.shape_class in ("circular_plate", "revolved"):
            lim = (spec.outer_diameter or (spec.outer_stack[0].diameter if spec.outer_stack else 0)) / 2
            outside = lim > 0 and (h.x**2 + h.y**2) ** 0.5 + r > lim + 0.5
        elif spec.shape_class == "rect_plate" and spec.length and spec.width:
            outside = not (-0.5 <= h.x <= spec.length + 0.5 and -0.5 <= h.y <= spec.width + 0.5)
        elif spec.shape_class == "profile_plate" and spec.profile_points:
            xs = [p[0] for p in spec.profile_points]; ys = [p[1] for p in spec.profile_points]
            if h.axis == "y":
                outside = not (min(xs) - 0.5 <= h.x <= max(xs) + 0.5)
            else:
                outside = not (min(xs) - 0.5 <= h.x <= max(xs) + 0.5
                               and min(ys) - 0.5 <= h.y <= max(ys) + 0.5)
        if outside:
            checks.append(f"穴({h.x:.1f},{h.y:.1f})が外形の外 — 座標解釈の失敗の可能性")
    if spec.shape_class == "circular_plate":
        if not close(spec.outer_diameter, bb.size.X):
            checks.append(f"外径不一致: spec={spec.outer_diameter} bbox={bb.size.X:.2f}")
        if not close(spec.thickness, bb.size.Z):
            checks.append(f"板厚不一致: spec={spec.thickness} bbox={bb.size.Z:.2f}")
    if spec.shape_class == "rect_plate":
        if not close(spec.length, bb.size.X):
            checks.append(f"長さ不一致: spec={spec.length} bbox={bb.size.X:.2f}")
        if not close(spec.width, bb.size.Y):
            checks.append(f"幅不一致: spec={spec.width} bbox={bb.size.Y:.2f}")
    return {
        "brep_valid": bool(ok),
        "volume_mm3": round(solid.volume, 1),
        "bbox": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
        "dimension_warnings": checks,
    }


# ------------------------------------------------------------------ 一括実行

def _build_pass(spec: ShapeSpec) -> dict:
    """ビルド+検証を1回試行し、結果を辞書で返す (solid含む)。"""
    out: dict = {"solid": None, "verification": None, "error": None, "ok": False}
    try:
        solid = build_from_spec(spec)
        v = verify(solid, spec)
        out["solid"] = solid
        out["verification"] = v
        out["ok"] = v["brep_valid"] and not v["dimension_warnings"]
    except SpecBuildError as e:
        out["error"] = ("spec", spec.unsupported_reason or str(e))
    except Exception as e:
        out["error"] = ("exc", str(e))
    return out


def run_interpret(dxfdoc, out_dir, base_name: str, sid: str,
                  cross_check: bool = False) -> dict:
    """ダンプ→解釈→ビルド→検証→エクスポート。

    品質チェックNG (検証警告・極小体積) の場合、もう片方のエンジンで1回だけ再解釈し、
    良い方の結果を採用する (エンジン間の得手不得手を自動吸収)。
    """
    import json as _json
    from pathlib import Path
    dump = generate_dump(dxfdoc)
    spec, usage = interpret(dump)
    build = _build_pass(spec)

    # ---- 品質フォールバック: 検証警告ありなら別エンジンで再解釈して比較
    used = usage.get("model", "")
    other = "claude" if used.startswith("gemini") else "gemini"
    other_key = "ANTHROPIC_API_KEY" if other == "claude" else "GEMINI_API_KEY"
    if (not build["ok"]) and build["error"] is None and os.environ.get(other_key):
        try:
            spec2, usage2 = interpret(dump, force=other)
            build2 = _build_pass(spec2)
            if build2["ok"] or (build2["verification"] and not build["verification"]):
                warn = (build["verification"] or {}).get("dimension_warnings", [])
                usage2["fallback_reason"] = (
                    f"{used}の結果が品質チェックNG({'; '.join(warn)[:120]})のため"
                    f"{other}で再解釈して採用")
                spec, usage, build = spec2, usage2, build2
        except Exception:
            pass  # 再解釈失敗時は初回結果を使う

    # クロスレビュー: 明示指定時、または「AIの自信が低い」解釈(仮定3件以上/図面矛盾あり)のとき自動発動。
    # Gemini自身が解釈エンジンだった場合は自己チェックになるためスキップ
    is_gemini_primary = usage.get("model", "").startswith("gemini")
    uncertain = len(spec.assumptions) >= 3 or len(spec.drawing_conflicts) >= 1
    do_cc = (cross_check or uncertain) and not is_gemini_primary
    gemini = gemini_cross_check(dump) if do_cc else None
    diffs = compare_specs(spec, gemini)

    result: dict = {
        "spec": spec.model_dump(),
        "usage": usage,
        "gemini_cross_check": gemini,
        "cross_check_diffs": diffs,
        "buildable": False,
        "step": None, "glb": None,
        "verification": build["verification"],
        "build_error": None,
        "cross_check_auto": bool(uncertain and not cross_check and gemini is not None),
    }
    out_dir = Path(out_dir)
    if build["solid"] is not None:
        from build123d import export_gltf, export_step
        step_path = out_dir / f"{base_name}_AI.step"
        glb_path = out_dir / f"{base_name}_AI.glb"
        export_step(build["solid"], str(step_path))
        export_gltf(build["solid"], str(glb_path), binary=True)
        result["buildable"] = True
        result["step"] = f"/api/file/{sid}/{step_path.name}"
        result["glb"] = f"/api/file/{sid}/{glb_path.name}"
    elif build["error"]:
        kind, msg = build["error"]
        result["build_error"] = (
            f"3Dデータは生成されていません(解釈のみ)。理由: {msg}" if kind == "spec"
            else f"3Dデータは生成されていません(ビルド処理でエラー): {msg}")

    # ---- 裏ログ: 解釈JSONを出力先に保存 (トラブル調査用)
    try:
        log = {k: result[k] for k in ("spec", "usage", "verification", "build_error",
                                      "buildable", "cross_check_diffs")}
        (out_dir / f"{base_name}_AI解釈ログ.json").write_text(
            _json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return result
