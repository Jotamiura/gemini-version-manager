# Gemini API Version Manager

社内GASプロジェクトで使用するGemini APIモデルのバージョンを一元管理するリポジトリ。

## 現在の本番モデル

```
gemini-3-flash-preview
```

## 管理対象プロジェクト

| # | プロジェクト | リポジトリ | ファイル:行 | デプロイ |
|---|---|---|---|---|
| 1 | BPO車検証OCR | `Jotamiura/BPO` | `gas/bpo-processor/ocr.js:8` | `clasp push` |
| 2 | 車検リネーマー（自動） | `Jotamiura/Syakensyo-Renamer` | `コード.gs:20` | `clasp push` |
| 3 | 承認リネーマー | `Jotamiura/Syakensyo-Renamer-with-check` | `ai_approval_workflow.gs:29` | `clasp push` |
| 4 | 保険受付票リネーマー | `gas-projects/hoken_uketukehyo` | `コード.gs:69` | `clasp push` |
| 5 | Hoken-QA | `Jotamiura/Hoken-QA` | `Hoken-QA/Code.js:69` | `clasp push` |
| 6 | 車両カタログBot | `Jotamiura/vehicle-catalogue-chatbot` | `test_gemini.py:8` | Vertex AI (Python) |

## スキャン実行

```bash
# 全プロジェクトのモデルバージョンを確認
bash scan.sh

# 不一致がある場合の更新ガイド表示
bash scan.sh --update
```

## モデル更新手順

1. Google AI の公式アナウンスで新モデルをテスト
2. `gemini-versions.json` の `production_model` を更新
3. `bash scan.sh` で不一致箇所を確認
4. 各プロジェクトのソースコードを更新
5. 各プロジェクトで `clasp push` を実行（GASデプロイ）
6. 各プロジェクトで `git commit && git push`
7. `gemini-versions.json` の `current_model` と `last_updated` を更新

## 非推奨モデル

| モデル | 廃止日 | 後継 |
|---|---|---|
| `gemini-2.5-flash-preview-09-2025` | 2026-03-31 | `gemini-3-flash-preview` |

## 注意事項

- マルチモーダル対応モデル（Flash以上）を選択すること（車検証OCR用途のため）
- エンドポイント: `https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key=`
- vehicle-catalogue-chatbot のみ Vertex AI SDK (Python) を使用。他は全て Google AI Studio API
