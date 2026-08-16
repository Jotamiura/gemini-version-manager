# Gemini API Version Manager

社内プロジェクト(GAS / Python / TS)で使用する Gemini API モデルを一元管理するリポジトリ。

## 方針(2026-08-15 三浦さん決定)

**全プロジェクト `-latest` エイリアスに統一**(`gemini-flash-latest` / `gemini-pro-latest`)。
Google 側の自動切替(2週間前告知)で常に最新実体へ追従する。

- 例外: Vertex AI 経由のプロジェクト(Hoken-QA・車両カタログBot)は `-latest` 別名が無いため固定
- 用途別の使い分け: 常用 = flash-latest / 精度が要る「判断」だけ pro-latest(保険OCR・粗利マッチング等)
- 決定の経緯・価格比較は Obsidian vault `25_技術スタック/Geminiモデル選定.md` 参照

## SSOT

**`gemini-versions.json` が唯一の正**。プロジェクト一覧・ファイル位置・現在モデル・デプロイ方法はすべてそこを見る
(この README には転記しない — 2026-08-15 に README 側の表が大幅陳腐化していたため廃止)。

- `alias_status`: `-latest` が実際にどのモデルを指しているかの実測記録
- `projects[].current_model`: 各プロジェクトのコード上の指定値。`bash scan.sh` はこの値と実コードを突き合わせる(2026-08-16〜。以前は単一 `production_model` と全プロジェクトを比較していたため、意図的に pro を使う組が毎回 MISMATCH と誤報されていた)
- `projects[].pattern`: 実コードの記法テンプレート。モデル値部分を `{{MODEL}}` で表す(例: `var GEMINI_MODEL = '{{MODEL}}';`)。`scan.sh` の検出パターンの正。**実コードの書き方(クォートの種類・変数名等)を変えたら、ここも合わせて更新すること** — ズレると NOT_FOUND になる
- `projects[].allow_fixed_model`: `true` を付けると `-latest` 統一の対象外として scan.sh の WARN を抑制する(Vertex AI 経由など `-latest` 別名が存在しないプロジェクト用。現状 Hoken-QA / 車両カタログBot(テスト) に設定済み)
- `projects[].local_path` が **この端末に存在しない**プロジェクト(Mac 側専用リポ等)は `scan.sh` 上で `SKIP` として区別される(`NOT_FOUND` = レジストリ記述と実コードのズレ、`SKIP` = そもそもこの端末で確認できない、を混同しないための区別)

## スクリプト

| スクリプト | 役割 |
|---|---|
| `bash scan.sh` | 全プロジェクトのコードを走査し、**各プロジェクトの `current_model`**(レジストリ側)と実コードの記述を突き合わせて確認。実処理は `scan_core.py`(下記) |
| `scan_core.py` | `scan.sh` から呼ばれるスキャナ本体。`projects[].pattern` の `{{MODEL}}` プレースホルダを正規表現化し、プロジェクトごとの実コード記法にピンポイントで一致させる(URLテンプレート `models/${model}` 等の誤検出を防ぐ設計。2026-08-16 刷新 issue #1) |
| `python check_latest_switch.py` | **-latest 実体の切替検知 + スモークテスト + Chatwork 通知**(下記) |
| `run_latest_check.ps1` | ↑ の Task Scheduler ラッパー(ログ: `logs/latest-check-*.log`) |
| `setup_latest_check_task.ps1` | 毎朝 8:20 のタスク `gemini-latest-switch-check` を登録(初回1回。2026-08-15 登録済み) |

## 切替検知フロー(2026-08-15 稼働開始)

毎朝 8:20 に自動実行:

1. 3エイリアス(`flash-latest` / `pro-latest` / `flash-lite-latest`)の実体を `modelVersion` で実測
2. 前回記録(`alias_state.json`)と比較。**変化なしなら無音**
3. 切替を検知したら新実体でスモークテストを実行:
   - 構造化JSON抽出(name/date/amount)が壊れていないか
   - 画像OCRで先頭ゼロ(`00395`)が保持されるか(`test_assets/ocr_sample.png`)
4. 結果を Chatwork マイチャットへ通知 → **各プロジェクトの dryRun 実行と採否判断は三浦さん**(検出は自動、判断は人間)

手動での動作確認: `python check_latest_switch.py --force-smoke --dry-run`

必要な環境変数: `GEMINI_API_KEY` / `CHATWORK_API_TOKEN_MIURA`(ユーザー環境変数に設定済み)

※ 監視系通知のため休業日カレンダー(SSOT)による停止は適用しない(case-hub Issue #15 ポリシーの「エラー・障害監視通知は止めない」枠)

## モデルを手動で変更する場合の手順

1. 対象プロジェクトの `gemini-versions.json` 内 `current_model` を新しい値に更新(意図的な決定。まずレジストリを書き換える)
2. `bash scan.sh` を実行 → 実コードとレジストリの差(MISMATCH)として検出されることを確認
3. 各プロジェクトのソースコードを更新(⚠️ **clasp push の前に必ず `git fetch`** — 別マシン更新の巻き戻し事故防止。vault `clasp運用の落とし穴` 参照)
4. 各プロジェクトで `clasp push` → `git commit && git push`
5. `bash scan.sh` を再実行し MATCH に戻ったことを確認、`gemini-versions.json` の `last_updated` を更新

`bash scan.sh --update` は MISMATCH のプロジェクト一覧と手動更新の手引きを表示するだけで、ファイルは一切書き換えない(各プロジェクトのソースは本スクリプトの管理外のため)。

## 注意事項

- マルチモーダル対応モデル(Flash 以上)を選択すること(帳票OCR用途のため)
- エンドポイント: `https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key=`
- Gemini 3.7 Flash 以降は thinking トークンが `maxOutputTokens` を消費するため、出力予算が小さいと本文が空になる(2026-08-15 実測)
