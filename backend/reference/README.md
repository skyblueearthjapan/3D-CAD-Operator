# reference/ — 実証済みリファレンス実装 (2026-07-13 セッションから保全)

引き継ぎ書 (Documents\3DCADオペレータ\CLAUDE.md) §6 ロードマップの実装資産。

- build_three.py   : ★gear_outline() = インボリュート歯形生成 (m5-27T旋回ギアで理論体積一致を実証)
                     + ドグ(カムプロファイル+端面ドリル) + ベアリングケース(段付き旋盤物)
- build_four.py    : 回転テーブル(ザグリ/タップ/リーマ) + LMカバー(破断図) + ストッパー(半径方向タップ)
                     + 防塵カバー(曲げ板金の手動実装 = Phase 2 bent_plate の工法見本)
- build_gear_cover.py : ギアフタ (皿ザグリ + C1面取り)
- fix_table.py     : 回転テーブル t32→t25 修正版 (PL=素材厚の教訓)
- inspect_any.py   : DXFエンティティダンプの原型 (app/ai_interpreter.py generate_dump に発展)
- gemini_validation.py : Gemini主エンジン8枚検証 (8/8成功)
