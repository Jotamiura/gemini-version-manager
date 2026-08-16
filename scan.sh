#!/bin/bash
# gemini-version-scanner.sh
# 全プロジェクトのGemini APIモデルバージョンを横断スキャンし、
# 「レジストリ(gemini-versions.json の current_model)」と「実コード」の不一致を検出する。
#
# 比較基準は projects[] 各要素の current_model(2026-08-16 刷新。旧: 単一 production_model 一律比較)。
# 検出は projects[].pattern (実コード記法の雛形) を使うため、意図的な pro/flash 使い分けを
# 誤報しない。実処理は scan_core.py に委譲し、このファイルは前段の防御(python検出・ロケール)のみ担う。
#
# 使い方:
#   bash scan.sh              # 通常スキャン
#   bash scan.sh --update     # MISMATCH のプロジェクトについて手動更新の手引きを表示(ファイルは書き換えない)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/gemini-versions.json"
CORE="$SCRIPT_DIR/scan_core.py"

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
  echo "ERROR: python が見つかりません(python3 は Windows Store のスタブの可能性があるため除外しています)"
  exit 1
fi

# Windows環境でPythonの日本語出力を正しく扱う
export PYTHONIOENCODING=utf-8

# LANG 未設定の環境(Claude Code の Bash ツール等)では grep -P が
# 「-P supports only unibyte and UTF-8 locales」で全滅することがあるため、
# UTF-8 ロケールを明示して防ぐ(scan_core.py 自体は grep -P に依存しないが、
# 呼び出し元シェルの他コマンドへの影響を避けるため維持)
export LC_ALL=C.UTF-8

# Git Bash パス (/c/...) → Windows パス (C:/...) に変換
to_win_path() {
  echo "$1" | sed 's|^/\([a-zA-Z]\)/|\1:/|'
}

CONFIG_WIN=$(to_win_path "$CONFIG")
CORE_WIN=$(to_win_path "$CORE")

"$PYTHON" "$CORE_WIN" "$CONFIG_WIN" "$@"
