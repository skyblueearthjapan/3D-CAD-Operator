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
    segment: Optional[int] = Field(
        None, description="bent_plate専用: 穴が開く直線区間 (0始まり, bend_path[i]→[i+1])")
    u: Optional[float] = Field(
        None, description="bent_plate専用: 区間始点(bend_path[segment])からの距離 [mm]")
    v: Optional[float] = Field(
        None, description="bent_plate専用: 幅方向位置 (手前端=0..width)。省略時=幅中央")
    note: Optional[str] = Field(None, description="元注記 (例 '12-14キリ ザグリ20深13 PCD378')")


class StackStep(BaseModel):
    """回転体の段 (直径×高さ)。"""
    diameter: float
    height: float


class HubStep(BaseModel):
    """歯車の歯部に付くハブ(ボス)段。"""
    diameter: float = Field(description="ハブ段の外径")
    height: float = Field(description="ハブ段の高さ(軸方向)")
    position: Literal["above", "below"] = Field(
        "above", description="歯部のどちら側に付くか: above=歯部上面から上へ / below=下面から下へ")


class KeywaySpec(BaseModel):
    """キー溝(JIS B 1301 平行キー、1本)。gear/revolved 共用。既定は内径(ボア)側。"""
    width: float = Field(description="溝幅 b")
    depth: float = Field(description="溝深さ。内径側は内径面から t2、軸側は外径面から t1")
    z_from: Optional[float] = Field(None, description="溝の下端 z(省略時 0=底面)")
    z_to: Optional[float] = Field(None, description="溝の上端 z(省略時=貫通)")
    angle_deg: float = Field(0.0, description="溝の周方向位置(既定 0=+X)")
    side: Literal["bore", "shaft"] = Field("bore", description="bore=内径側(既定) / shaft=軸外径側")
    bore_diameter: Optional[float] = Field(None, description="内径側の基準ボア径(省略時 bore_stack から推定)")
    shaft_diameter: Optional[float] = Field(None, description="軸側の外径(side='shaft' で必須)")


class RadialHoleSpec(BaseModel):
    """半径方向(軸に直交)の穴/タップ。ホーロー(セットスクリュ)・止めねじ等。revolved用。"""
    z: float = Field(description="軸方向高さ(底面 z=0 から)。穴の中心高さ")
    angle_deg: float = Field(0.0, description="周方向角度(0=+X, 反時計回り/CCW)")
    diameter: Optional[float] = Field(None, description="キリ穴径。タップのみなら null 可")
    thread: Optional[str] = Field(None, description="ねじ呼び 'M6' 等(タップの場合。下穴はTAP_DRILLで決定)")
    depth: Optional[float] = Field(
        None, description="外周面からの掘込み深さ。省略時は軸中心まで(片側のみ)。"
                          "ホーローや止めねじは壁厚+α 程度を指定")
    note: Optional[str] = Field(None, description="元注記(例 'M6ホーロー' '2-M6')")


class ShapeSpec(BaseModel):
    """図面から解釈した 3D 形状仕様。"""
    part_name: str = Field(description="部品名")
    material: Optional[str] = Field(None, description="材質・熱処理")
    shape_class: Literal["circular_plate", "rect_plate", "profile_plate",
                         "revolved", "gear", "bent_plate", "rolled_plate",
                         "not_a_part", "unsupported"] = Field(
        description="circular_plate=円板+穴 / rect_plate=矩形板+穴 / "
                    "profile_plate=任意多角形輪郭の板+穴 / revolved=旋盤物(段付き円筒) / "
                    "gear=外歯平歯車(インボリュート歯形を自動生成) / "
                    "bent_plate=直線曲げ板金(L/コ/ハット断面。曲げ線は全て平行=1方向) / "
                    "rolled_plate=円弧曲げ板金(単一曲率の円筒殻セクタ。R曲げカバー等) / "
                    "not_a_part=単品加工部品の図面でない(組立図・購入品・ステッカー・配線経路図等) / "
                    "unsupported=単品部品だが自動3D化未対応(内歯車・複数方向曲げ・複数曲率曲げ・複合形状など)")
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
    # --- gear (shape_class='gear') 用 ---
    module: Optional[float] = Field(None, description="歯車のモジュール m (gearで必須)")
    teeth: Optional[int] = Field(None, description="歯車の歯数 z (gearで必須)")
    pressure_angle_deg: float = Field(20.0, description="圧力角 [deg] (歯車。既定20)")
    face_width: Optional[float] = Field(
        None, description="歯幅(歯部の軸方向長さ。gearで必須)。断面図/側面図の歯部幅")
    tip_diameter: Optional[float] = Field(
        None, description="歯先円径(外径)。省略時 m*(z+2) を使用。図面のOD/歯先円寸法があれば入れる")
    root_diameter: Optional[float] = Field(
        None, description="歯底円径。省略時 m*(z-2.5)。図面に歯底寸法があれば入れる")
    hub_stack: Optional[list[HubStep]] = Field(
        None, description="歯車のハブ(ボス)段。歯部の上/下に付く円筒段を列挙")
    keyway: Optional[KeywaySpec] = Field(
        None, description="キー溝(gear=内径 / revolved=内径or軸)。1本のみ")
    radial_holes: list[RadialHoleSpec] = Field(
        default_factory=list, description="revolved の半径方向穴/タップ(ホーロー・止めねじ等)")
    # --- bent_plate (直線曲げ板金) 用 ---
    bend_path: Optional[list[list[float]]] = Field(
        None, description="bent_plate の断面中立線(板厚中心)の折れ線頂点 [[x,y],...] "
                          "(開いた折れ線・曲げ平面内)。曲げ順に並べる。thickness=板厚, width=奥行き幅")
    bend_radii: Optional[list[float]] = Field(
        None, description="bent_plate の各曲げ(中間頂点)の内曲げR。要素数=bend_path頂点数-2。"
                          "省略時は板厚と同値を仮定")
    # --- rolled_plate (円弧曲げ板金) 用 ---
    arc_radius: Optional[float] = Field(
        None, description="rolled_plate: 曲げの基準半径。radius_ref でどの面かを指定")
    radius_ref: Literal["inner", "outer", "neutral"] = Field(
        "inner", description="rolled_plate: arc_radius が 内面/外面/中立面 のどれか")
    arc_angle_deg: Optional[float] = Field(
        None, description="rolled_plate: 曲げの開き角(度)。角度寸法から読む。360未満")
    straight_ends: Optional[list[float]] = Field(
        None, description="rolled_plate: 両端の接線直線(フラット)延長長さ [始端,終端]。無ければ null")
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
- **ビュー間で形状クラス自体が矛盾する場合(平面図は単純な板に見えるが断面図/側面図に
  曲げフランジ・ハット断面が描かれている等)は、断面図を正とする**。安易に平板(rect/profile)を
  選ばないこと。断面が曲げを示すなら bent_plate、確信が持てなければ unsupported にして
  drawing_conflicts に矛盾を記録する
- PCD上の等配穴は1穴ずつ座標展開して holes に列挙する(count表現ではない)。
  **開始角(位相)を勝手に仮定しないこと**: 座標は必ず図面に作図された円の実座標から取る
  (例: 4穴等配が45°/135°/225°/315°に描かれているのに0°/90°/180°/270°と書くのは誤り)
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
- 外歯車(平歯車・ピニオン) → gear。判定根拠: 注記 "ｍ○-○Ｔ"(モジュール-歯数)や
  "モジュール""歯数"、メーカー型式(KHK SSx-yy 等)、歯先円=実線・ピッチ円=一点鎖線の同心円。
  * module=モジュール(ｍの後の数)、teeth=歯数(Ｔの前の数)、pressure_angle_deg は明記なければ20
  * tip_diameter(歯先円/外径)は m*(teeth+2) で検算する(例 m6 z34 → 216)。図面のODと
    一致しなければ歯数/モジュールの読み違いを疑う。それでも図面ODが正なら図面値を tip_diameter に
    入れ、転位歯車の可能性として assumptions に記録する
  * face_width(歯幅)=側面図/断面図の歯部の軸方向長さ。内径は bore_stack(上面=ハブ含む全体の
    上面から下へ段を列挙)。ボルト穴・タップ(PCD等配)は holes に1穴ずつ展開
  * キー溝(幅寸法+内径断面の矩形切欠き)は keyway に width/depth。ハブ(ボス)段は hub_stack に
    diameter/height/position(above/below)で列挙
- 内歯車(リング内側に歯・インターナルギア) → unsupported のまま(unsupported_reason に「内歯車のため未対応」)
- 直線曲げ板金(1方向の折り曲げでできる L字・コの字(チャンネル)・ハット/Z断面) → bent_plate
  判定根拠: 側面図(断面図)に板厚ぶん離れた2本の平行ポリラインが折れ曲がって描かれ、
  各折れ角に曲げRの円弧がある。もう一方のビューは一定断面の押し出し(=幅 width)に見える。
  注記「FB6×50」「PL○ 曲げ」等も有力。
  * bend_path: 断面の中立線(板厚中心線)を折れ順に頂点列で書く。原点は任意。
    図面が内面/外面の寸法しか示さない場合は板厚中心へ換算する(外面基準なら内側へt/2寄せる)。
    フランジ先端の端点は板厚中心線上の終端位置に置く(端は板厚中心から±t/2の平坦カット)
  * bend_radii: 各曲げの内側R。不明なら省略(板厚と同値を仮定)
  * thickness=板厚(平行2線の間隔), width=押し出し方向の奥行き。破断図なら実寸上書き値を使う
  * 穴は holes に segment(区間番号: bend_pathのi→i+1)+u(区間始点からの距離)+v(幅方向0..width、
    中央は省略)で書き、**x=0, y=0 で固定する**(bent_plateではx/yを使わない)。
    diameter か thread を入れる。板厚方向貫通のみ対応。曲げRにかかる穴・斜め穴は unmodeled_features へ
  * 2方向以上の曲げ・箱曲げ、リブ/絞り/溶接組立を伴うものは bent_plate にせず unsupported
- 円弧に沿って曲げた板金カバー(R曲げ・巻き板金) → rolled_plate:
  側面図(端面図)に「同心の円弧ペア」が現れるのが目印(外側実線=外面)。
  * arc_radius: 側面図の半径寸法。外面寸法なら radius_ref='outer'、内面なら 'inner'、
    曲げ中心線なら 'neutral'。どの面か図面から確定できなければ assumptions に記録
  * arc_angle_deg: 角度寸法(例 '120°')を読む。無ければ円弧の start/end 角から算出(360未満)
  * thickness=素材PL値(断面の仕上げ厚優先), width=軸方向の長さ(曲げ線に平行な辺の寸法)
  * straight_ends: 両端にフラット平板部が続く場合その長さ [始端,終端]。無ければ null
  * 適用条件: 曲率が単一で、ねじれ・二重曲げ・球面が無いこと。複数曲率の共存や
    半径方向穴が主体なら unsupported。曲面上の穴・タップは作らず unmodeled_features に列挙し、
    曲面穴が主要な機能(4件以上等)の部品は unsupported に倒す
- **単品加工部品の図面でないもの → not_a_part**: 組立図(○○組立図/全体図/ASSY・部品表で複数部品を並べたもの)、
  購入品の参考図・追加工前提の購入品図(ベアリング/カップリング/ボールネジ/LMガイド等の型番主体図)、
  ステッカー・銘板、配線/配管の経路図・レイアウト図、複数部材の溶接構造物の全体図。
  unsupported_reason に図面の種別(例「回転軸減速機の組立図」)を必ず書く
- 旋盤物に半径方向タップ・止めねじ穴・キー溝が付く場合は revolved のまま継続:
  半径方向穴は radial_holes に、キー溝は keyway に入れる。unsupported にするのは
  「外形形状そのものが対応外(内歯車・複数方向曲げ・カム・溶接構造物等)」の場合のみ
- 上記いずれにも該当しない複合形状 → unsupported (その場合も判る限り各フィールドを埋め、unmodeled_features に詳細を書き、
  unsupported_reason に「なぜ自動3D化できないか・人が3D化する際のポイント」を日本語で必ず書く)

revolved の付加特徴(半径方向穴・キー溝)の読み方:
- 半径方向タップ/ホーロー(止めねじ): 側面図で外周の中心線上に描かれた小円+ねじ注記(例 M6ホーロー)。
  radial_holes に {z(軸方向高さ), angle_deg(周方向。図面に指定があればその角度), thread または diameter, depth} を入れる。
  ホーローの depth は片側壁厚+2mm 程度。**等配穴は1本ずつ角度展開して列挙**
  (例 3-M6等配 → angle_deg 0/120/240 の3要素)。
  **depth 省略は「外周面から軸中心まで片側のみ」** — 対面2方向から掘る場合は2本個別に指定
- 内径キー溝: 正面図/断面図でボア内側の矩形切欠き。keyway に {width, depth(内径面から), z_from/z_to(貫通なら省略), side='bore'} 。
  JIS B 1301 平行キー常識値: 軸φ22〜30→キー8(t2=3.3)、φ30〜38→10、φ38〜44→12、φ44〜50→14、
  φ50〜58→16、φ58〜65→18(t2=4.4)、φ65〜75→20。図面の明示寸法を優先、補完時は assumptions に記録
- 軸側キー溝は side='shaft' + shaft_diameter を指定
- M40等の大径めねじは、ねじ山を作らず下穴径の円筒として bore_stack に表現してよい(下穴近似)

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
        # max_tokensが大きいと非ストリーミングはSDKが拒否する(10分制約)ためstreamで実行
        with client.messages.stream(
            model=AI_MODEL,
            max_tokens=32000,  # adaptive thinkingが大きく消費しても本文が切れない余裕を確保
            thinking={"type": "adaptive"},
            system=system,
            messages=messages,
            **extra,
        ) as stream:
            resp = stream.get_final_message()
        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude が応答を拒否しました (安全性判定)")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text.strip():
            # 思考でトークンを使い切る等で本文が空 → リトライへ (実測で複雑図面時に発生)
            last_err = RuntimeError(f"Claude応答が空 (stop_reason={resp.stop_reason})")
            messages = [{"role": "user", "content": dump
                         + "\n\n(注意: 思考は最小限にし、JSONのみを直接出力してください)"}]
            continue
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
    g_mod = num(gemini.get("module"))
    if spec.module and g_mod and abs(spec.module - g_mod) > 0.05:
        diffs.append(f"モジュール: Claude={spec.module} / Gemini={g_mod}")
    g_teeth = num(gemini.get("teeth"))
    if spec.teeth and g_teeth and spec.teeth != int(g_teeth):
        diffs.append(f"歯数: Claude={spec.teeth} / Gemini={int(g_teeth)}")
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
            # Phase4: 半径方向穴・キー溝 (アクティブなBuildPart内で減算する)
            from .revolved_features import apply_keyway, apply_radial_holes
            if spec.radial_holes:
                apply_radial_holes(p, [r.model_dump() for r in spec.radial_holes],
                                   [s.model_dump() for s in spec.outer_stack])
            if spec.keyway:
                apply_keyway(p, spec.keyway.model_dump(),
                             [s.model_dump() for s in (spec.bore_stack or [])])
        return p.part

    if spec.shape_class == "gear":
        if not (spec.module and spec.teeth and spec.face_width):
            raise SpecBuildError("gear には module / teeth / face_width が必要です")
        from . import gear_builder
        params = {
            "module": spec.module,
            "teeth": spec.teeth,
            "pressure_angle_deg": spec.pressure_angle_deg,
            "face_width": spec.face_width,
            "tip_diameter": spec.tip_diameter,
            "root_diameter": spec.root_diameter,
            "hub_stack": [h.model_dump() for h in spec.hub_stack] if spec.hub_stack else [],
            "bore_stack": ([s.model_dump() for s in spec.bore_stack]
                           if spec.bore_stack else None),
            "keyway": spec.keyway.model_dump() if spec.keyway else None,
            "keyway_angle_deg": spec.keyway.angle_deg if spec.keyway else 0.0,
            "holes": [h.model_dump() for h in spec.holes],
        }
        try:
            return gear_builder.build_gear(params)
        except gear_builder.GearBuildError as e:
            raise SpecBuildError(f"歯車ビルド失敗: {e}")

    if spec.shape_class == "bent_plate":
        if not (spec.bend_path and spec.thickness and spec.width):
            raise SpecBuildError("bent_plate には bend_path / thickness / width が必要です")
        from .bent_builder import BentPlateError, build_bent_plate
        holes = [{"segment": h.segment if h.segment is not None else 0,
                  "u": h.u if h.u is not None else 0.0, "v": h.v,
                  "diameter": h.diameter, "thread": h.thread,
                  "through": h.through, "depth": h.depth} for h in spec.holes]
        try:
            return build_bent_plate({
                "thickness": spec.thickness, "width": spec.width,
                "profile_path": spec.bend_path, "bend_radii": spec.bend_radii,
                "holes": holes,
            })
        except BentPlateError as e:
            raise SpecBuildError(str(e))

    if spec.shape_class == "rolled_plate":
        if not (spec.arc_radius and spec.thickness and spec.arc_angle_deg and spec.width):
            raise SpecBuildError(
                "rolled_plate には arc_radius/thickness/arc_angle_deg/width が必要です")
        from .rolled_builder import RolledParams, build_rolled_plate
        try:
            return build_rolled_plate(RolledParams(
                arc_radius=spec.arc_radius,
                thickness=spec.thickness,
                arc_angle_deg=spec.arc_angle_deg,
                width=spec.width,
                radius_ref=spec.radius_ref,
                straight_ends=spec.straight_ends or [0.0, 0.0]))
        except ValueError as e:
            raise SpecBuildError(f"円弧曲げビルド失敗: {e}")

    if spec.shape_class == "not_a_part":
        raise SpecBuildError(
            "単品加工部品の図面ではありません(3D化対象外): "
            + (spec.unsupported_reason or "組立図・購入品・ステッカー等"))
    raise SpecBuildError(
        "この形状クラスは自動ビルド未対応です: " + spec.shape_class +
        (" / 未対応特徴: " + "; ".join(spec.unmodeled_features) if spec.unmodeled_features else ""))


def verify(solid, spec: ShapeSpec, dxfdoc=None) -> dict:
    """BRepCheck + バウンディングボックス照合 + 健全性チェック + 2D投影照合。

    dxfdoc を渡すと投影照合 (生成ソリッドのXY投影 vs 図面の検出輪郭) も実施し、
    不一致は dimension_warnings に加わる → 品質フォールバックの判定にも効く。
    """
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
        if spec.shape_class in ("circular_plate", "revolved", "gear"):
            if spec.shape_class == "gear":
                od = spec.tip_diameter or ((spec.module * (spec.teeth + 2))
                                           if spec.module and spec.teeth else 0)
            else:
                # revolved はフランジ等の最大段径が外形限界
                od = spec.outer_diameter or (max(s.diameter for s in spec.outer_stack)
                                             if spec.outer_stack else 0)
            lim = od / 2
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
    if spec.shape_class == "gear":
        if spec.module and spec.teeth:
            od_theory = spec.module * (spec.teeth + 2)  # 標準平歯車の歯先円
            od_bbox = max(bb.size.X, bb.size.Y)
            od_expect = spec.tip_diameter or od_theory
            if abs(od_bbox - od_expect) > max(1.0, od_expect * 0.02):
                checks.append(f"歯先円径不一致: spec={od_expect:.1f} bbox={od_bbox:.2f}")
            if spec.tip_diameter and abs(spec.tip_diameter - od_theory) > max(1.0, od_theory * 0.02):
                checks.append(
                    f"歯先円径がm(z+2)と不一致: spec={spec.tip_diameter} 理論={od_theory:.1f}"
                    " — モジュール/歯数の読み違い(または転位歯車)の可能性")
        if spec.face_width and not spec.hub_stack \
                and not close(spec.face_width, bb.size.Z):
            checks.append(f"歯幅不一致: spec={spec.face_width} bbox.Z={bb.size.Z:.2f}")
    if spec.shape_class == "bent_plate" and spec.bend_path:
        if not close(spec.width, bb.size.Z):
            checks.append(f"幅不一致: spec={spec.width} bbox.Z={bb.size.Z:.2f}")
        xs = [p[0] for p in spec.bend_path]
        ys = [p[1] for p in spec.bend_path]
        if spec.thickness and max(max(xs) - min(xs), max(ys) - min(ys)) < spec.thickness:
            checks.append("断面外形が板厚より小さい — bend_path 座標の解釈失敗の可能性")
        # 断面bboxは中立線範囲〜+板厚の帯域に収まるはず (自由端は平坦カットで+0、
        # 垂直フランジの板厚張り出しで最大+t)。逸脱は座標破綻
        if spec.thickness:
            for label, actual, ext in (("X", bb.size.X, max(xs) - min(xs)),
                                       ("Y", bb.size.Y, max(ys) - min(ys))):
                if actual < ext - 0.5 or actual > ext + spec.thickness + 0.5:
                    checks.append(
                        f"断面{label}寸法不整合: bbox={actual:.1f} vs 中立線範囲{ext:.1f}"
                        f"(+t={spec.thickness}以内のはず) — bend_path座標の解釈失敗の可能性")
        try:
            from .bent_builder import developed_length
            dl = developed_length(spec.bend_path, spec.bend_radii, spec.thickness)
            gross = dl * (spec.thickness or 0) * (spec.width or 0)
            if gross > 0 and solid.volume > gross * 1.02:
                checks.append("体積が展開想定を超過 — bend_path 座標の解釈失敗の可能性")
            if gross > 0 and solid.volume < gross * 0.5:
                checks.append("体積が展開想定の半分未満 — 穴/座標の解釈失敗の可能性")
        except Exception:
            checks.append("bend_path から展開長を計算できません — 座標解釈の失敗の可能性")
    if spec.shape_class == "revolved":
        if spec.keyway:
            kw = spec.keyway
            if kw.side == "bore":
                r_bore = (kw.bore_diameter / 2) if kw.bore_diameter else (
                    min(s.diameter for s in spec.bore_stack) / 2 if spec.bore_stack else 0)
                if r_bore <= 0:
                    checks.append("キー溝(内径側)だがボア径が不明 — bore_stack か bore_diameter が必要")
                elif kw.width > 2 * r_bore:
                    checks.append(f"キー溝幅{kw.width}がボア径{2*r_bore:.1f}超 — 幅/内径の解釈ミスの可能性")
                elif kw.depth > r_bore:
                    checks.append(f"キー溝深さ{kw.depth}がボア半径{r_bore:.1f}超 — 深さ過大の可能性")
        if spec.outer_stack:
            r_out = max(s.diameter for s in spec.outer_stack) / 2
            total_h_r = sum(s.height for s in spec.outer_stack)
            for rh in spec.radial_holes:
                if rh.depth and rh.depth > 2 * r_out + 0.5:
                    checks.append(f"半径方向穴 depth={rh.depth} が直径{2*r_out:.1f}超 — 深さ過大の可能性")
                if not (0 <= rh.z <= total_h_r + 0.5):
                    checks.append(f"半径方向穴 z={rh.z} が部品高さ範囲(0〜{total_h_r:.1f})外 — z座標の解釈ミスの可能性")
    if spec.shape_class == "rolled_plate":
        if not close(spec.width, bb.size.Z):
            checks.append(f"幅(軸方向)不一致: spec={spec.width} bbox.Z={bb.size.Z:.2f}")
        try:
            from .rolled_builder import RolledParams, theoretical_volume
            theo = theoretical_volume(RolledParams(
                arc_radius=spec.arc_radius, thickness=spec.thickness,
                arc_angle_deg=spec.arc_angle_deg, width=spec.width,
                radius_ref=spec.radius_ref,
                straight_ends=spec.straight_ends or [0.0, 0.0]))
            if theo > 0 and abs(solid.volume - theo) / theo > 0.01:
                checks.append(f"体積が理論値と不一致: 実{solid.volume:.0f} / 理論{theo:.0f}mm3")
        except Exception as e:
            checks.append(f"円弧曲げの理論体積照合ができません: {str(e)[:80]}")
    projection = None
    if dxfdoc is not None:
        try:
            from .projection_check import check_projection
            projection = check_projection(solid, spec, dxfdoc)
            checks.extend(projection["warnings"])
        except Exception as e:  # 照合自体の失敗で本処理を止めない
            projection = {"status": "error", "warnings": [], "error": str(e)[:200]}
    return {
        "brep_valid": bool(ok),
        "volume_mm3": round(solid.volume, 1),
        "bbox": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
        "dimension_warnings": checks,
        "projection": projection,
    }


# ------------------------------------------------------------------ 一括実行

def _build_pass(spec: ShapeSpec, dxfdoc=None) -> dict:
    """ビルド+検証を1回試行し、結果を辞書で返す (solid含む)。"""
    out: dict = {"solid": None, "verification": None, "error": None, "ok": False}
    try:
        solid = build_from_spec(spec)
        v = verify(solid, spec, dxfdoc)
        out["solid"] = solid
        out["verification"] = v
        out["ok"] = v["brep_valid"] and not v["dimension_warnings"]
    except SpecBuildError as e:
        out["error"] = ("spec", spec.unsupported_reason or str(e))
    except Exception as e:
        out["error"] = ("exc", str(e))
    return out


def build_pass_with_fix(spec: ShapeSpec, dxfdoc=None) -> tuple[ShapeSpec, dict]:
    """ビルド+検証し、投影照合が穴の不一致を出したら図面吸着の自動補正を1回試す。

    補正は位置のみ (projection_check.snap_fix)。補正後に再ビルド+再照合し、
    警告が消える/減る場合のみ採用。採用時は spec.assumptions に補正内容を記録し
    (UIの⚠仮定にそのまま表示される)、build["auto_fix"] にも同じメモを入れる。
    """
    build = _build_pass(spec, dxfdoc)
    proj = (build["verification"] or {}).get("projection") if build["verification"] else None
    if (build["solid"] is None or build["ok"] or dxfdoc is None
            or not proj or proj.get("status") != "holes_missing"):
        return spec, build
    try:
        from .projection_check import snap_fix
        fixed = snap_fix(spec, dxfdoc, proj)
    except Exception:
        fixed = None
    if not fixed:
        return spec, build
    new_holes, notes = fixed
    note = "投影照合による自動補正(穴位置を図面の円に吸着): " + "; ".join(notes)
    spec2 = spec.model_copy(update={
        "holes": new_holes, "assumptions": spec.assumptions + [note]})
    build2 = _build_pass(spec2, dxfdoc)
    w1 = len((build["verification"] or {}).get("dimension_warnings", []))
    w2 = (len((build2["verification"] or {}).get("dimension_warnings", []))
          if build2["verification"] else w1 + 1)
    if build2["ok"] or w2 < w1:
        build2["auto_fix"] = note
        return spec2, build2
    return spec, build


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
    spec, build = build_pass_with_fix(spec, dxfdoc)

    # ---- 品質フォールバック: 検証警告ありなら別エンジンで再解釈して比較
    used = usage.get("model", "")
    other = "claude" if used.startswith("gemini") else "gemini"
    other_key = "ANTHROPIC_API_KEY" if other == "claude" else "GEMINI_API_KEY"
    if (not build["ok"]) and build["error"] is None and os.environ.get(other_key):
        try:
            spec2, usage2 = interpret(dump, force=other)
            spec2, build2 = build_pass_with_fix(spec2, dxfdoc)
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
        "auto_fix": build.get("auto_fix"),
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
                                      "buildable", "auto_fix", "cross_check_diffs")}
        (out_dir / f"{base_name}_AI解釈ログ.json").write_text(
            _json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return result
