#!/bin/bash
# gemini-version-scanner.sh
# 全プロジェクトのGemini APIモデルバージョンを横断スキャンし、不一致を検出する
#
# 使い方:
#   bash scan.sh              # 通常スキャン
#   bash scan.sh --update     # production_model に一括更新（dry-run表示後に確認）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/gemini-versions.json"

# python を検出（Windows Store のスタブを除外）
find_python() {
  for cmd in python python3; do
    local p=$(command -v "$cmd" 2>/dev/null)
    if [ -n "$p" ] && echo "$p" | grep -qv "WindowsApps"; then
      echo "$p"
      return
    fi
  done
}
PYTHON=$(find_python)
if [ -z "$PYTHON" ]; then
  echo "ERROR: python が見つかりません"
  exit 1
fi

# Windows環境でPythonの日本語出力を正しく扱う
export PYTHONIOENCODING=utf-8

# Git Bash パス (/c/...) → Windows パス (C:/...) に変換
to_win_path() {
  echo "$1" | sed 's|^/\([a-zA-Z]\)/|\1:/|'
}

CONFIG_WIN=$(to_win_path "$CONFIG")

# 設定読み込み
PRODUCTION_MODEL=$($PYTHON -c "
import json
with open('$CONFIG_WIN', encoding='utf-8') as f:
    data = json.load(f)
print(data['production_model'])
")

PROJECT_COUNT=$($PYTHON -c "
import json
with open('$CONFIG_WIN', encoding='utf-8') as f:
    data = json.load(f)
print(len(data['projects']))
")

echo "========================================"
echo " Gemini API Version Scanner"
echo " Production model: $PRODUCTION_MODEL"
echo " Scan date: $(date '+%Y-%m-%d %H:%M')"
echo "========================================"
echo ""

MISMATCH=0
OK=0
NOTFOUND=0
RESULTS=()

for i in $(seq 0 $((PROJECT_COUNT - 1))); do
  PROJECT_INFO=$($PYTHON -c "
import json
with open('$CONFIG_WIN', encoding='utf-8') as f:
    data = json.load(f)
p = data['projects'][$i]
print(p['name'])
print(p.get('local_path') or 'NONE')
print(p['file'])
print(p['line'])
print(p.get('repo', 'unknown'))
print(p.get('current_model', 'unknown'))
")

  NAME=$(echo "$PROJECT_INFO" | sed -n '1p')
  LOCAL_PATH=$(echo "$PROJECT_INFO" | sed -n '2p')
  FILE=$(echo "$PROJECT_INFO" | sed -n '3p')
  LINE=$(echo "$PROJECT_INFO" | sed -n '4p')
  REPO=$(echo "$PROJECT_INFO" | sed -n '5p')
  EXPECTED=$(echo "$PROJECT_INFO" | sed -n '6p')

  # ローカルパスがない場合はGitHub APIでチェック
  if [ "$LOCAL_PATH" = "NONE" ]; then
    echo "[$NAME] (GitHub: $REPO)"
    # URL-encode the file path for Japanese characters
    ENCODED_FILE=$($PYTHON -c "import urllib.parse; print(urllib.parse.quote(\"$FILE\"))")
    ACTUAL_MODEL=$("/c/Program Files/GitHub CLI/gh.exe" api "repos/$REPO/contents/$ENCODED_FILE" -q '.content' 2>/dev/null \
      | tr -d '\n\r ' \
      | base64 -d 2>/dev/null \
      | grep -oP 'models/\K[^:]+' \
      | head -1 \
      || echo "FETCH_FAILED")

    if [ "$ACTUAL_MODEL" = "FETCH_FAILED" ]; then
      echo "  WARNING: GitHub から取得できませんでした"
      NOTFOUND=$((NOTFOUND + 1))
    elif [ "$ACTUAL_MODEL" = "$PRODUCTION_MODEL" ]; then
      echo "  OK: $ACTUAL_MODEL"
      OK=$((OK + 1))
    else
      echo "  MISMATCH: $ACTUAL_MODEL (expected: $PRODUCTION_MODEL)"
      MISMATCH=$((MISMATCH + 1))
      RESULTS+=("$NAME|$REPO|$FILE:$LINE|$ACTUAL_MODEL")
    fi
  else
    FULL_PATH="$LOCAL_PATH/$FILE"
    echo "[$NAME] $FULL_PATH"

    if [ ! -f "$FULL_PATH" ]; then
      echo "  WARNING: ファイルが見つかりません"
      NOTFOUND=$((NOTFOUND + 1))
      continue
    fi

    # ファイルから実際のモデル名を抽出
    ACTUAL_MODEL=$(grep -oP 'models/\K[^:/"'"'"']+' "$FULL_PATH" 2>/dev/null | head -1 || true)

    if [ -z "$ACTUAL_MODEL" ]; then
      # Vertex AI SDK パターン (Python)
      ACTUAL_MODEL=$(grep -oP 'GenerativeModel\("\K[^"]+' "$FULL_PATH" 2>/dev/null | head -1 || true)
    fi

    if [ -z "$ACTUAL_MODEL" ]; then
      # var GEMINI_MODEL = 'xxx'; パターン
      ACTUAL_MODEL=$(grep -oP "GEMINI_MODEL\s*=\s*'\\K[^']+" "$FULL_PATH" 2>/dev/null | head -1 || true)
    fi

    if [ -z "$ACTUAL_MODEL" ]; then
      echo "  WARNING: モデル名を検出できませんでした"
      NOTFOUND=$((NOTFOUND + 1))
      continue
    fi

    if [ "$ACTUAL_MODEL" = "$PRODUCTION_MODEL" ]; then
      echo "  OK: $ACTUAL_MODEL"
      OK=$((OK + 1))
    else
      echo "  MISMATCH: $ACTUAL_MODEL (expected: $PRODUCTION_MODEL)"
      MISMATCH=$((MISMATCH + 1))
      RESULTS+=("$NAME|$LOCAL_PATH/$FILE:$LINE|$ACTUAL_MODEL")
    fi
  fi
done

echo ""
echo "========================================"
echo " Results: OK=$OK  MISMATCH=$MISMATCH  NOT_FOUND=$NOTFOUND"
echo "========================================"

if [ $MISMATCH -gt 0 ]; then
  echo ""
  echo "--- 不一致のあるプロジェクト ---"
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r pname ploc pmodel <<< "$r"
    echo "  $pname"
    echo "    場所: $ploc"
    echo "    現在: $pmodel → 推奨: $PRODUCTION_MODEL"
  done
  echo ""

  if [ "${1:-}" = "--update" ]; then
    echo "更新を実行しますか？ (y/N)"
    read -r answer
    if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
      echo "更新処理は各リポジトリで手動実行してください:"
      for r in "${RESULTS[@]}"; do
        IFS='|' read -r pname ploc pmodel <<< "$r"
        echo "  - $ploc: $pmodel → $PRODUCTION_MODEL"
      done
      echo ""
      echo "更新後、各プロジェクトで clasp push を忘れずに実行してください。"
    fi
  fi

  exit 1
fi

echo ""
echo "全プロジェクトが production model ($PRODUCTION_MODEL) と一致しています。"
exit 0
