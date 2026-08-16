#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scan_core.py — gemini-versions.json の各プロジェクトについて、
「レジストリの current_model」と「実コードに書かれているモデル値」を
プロジェクトごとの pattern (gemini-versions.json の projects[].pattern) を使って
突き合わせるスキャナ本体。

scan.sh から呼ばれる。単独実行も可: `python scan_core.py <gemini-versions.json のパス> [--update]`

設計方針(Issue #1 対応):
- 比較基準は「全プロジェクト一律 production_model」ではなく「各プロジェクトの current_model」
- 検出は projects[].pattern の {{MODEL}} プレースホルダを正規表現化し、
  そのプロジェクトの実際のコード記法にピンポイントで一致させる
  (models/${model} のような URL テンプレートを誤検出しない設計)
- local_path が存在しない(別マシン想定)プロジェクトは NOT_FOUND ではなく SKIP として区別する
- -latest 以外の固定モデル指定が残っていたら WARN を出す
  (allow_fixed_model: true が付いたプロジェクト = Vertex AI 等の統一対象外は除外)
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys

GH_CANDIDATES = [
    "gh",
    r"C:\Program Files\GitHub CLI\gh.exe",
]


def to_win_path(path):
    """Git Bash 形式のパス (/c/Users/...) を Windows 形式 (C:/Users/...) に変換する。
    既に Windows 形式ならそのまま返す。"""
    if not path:
        return path
    m = re.match(r"^/([a-zA-Z])(/.*)?$", path)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2) or ""
        return f"{drive}:{rest}"
    return path


def pattern_to_regex(pattern):
    """gemini-versions.json の pattern 文字列 (例: "var GEMINI_MODEL = '{{MODEL}}';")
    を、{{MODEL}} 部分だけをキャプチャする正規表現にコンパイルする。
    それ以外の文字はすべてリテラルとして re.escape する。"""
    MARKER = "{{MODEL}}"
    parts = pattern.split(MARKER)
    if len(parts) != 2:
        # マーカーが無い/複数ある想定外パターンはそのまま逐語一致を試みる(マッチしない)
        return re.compile(re.escape(pattern))
    # モデル名は英数字・ドット・ハイフンのみ(gemini-3.1-pro-preview-001 等を許容)
    regex = re.escape(parts[0]) + r"([A-Za-z0-9_.\-]+)" + re.escape(parts[1])
    return re.compile(regex)


def find_gh():
    for cand in GH_CANDIDATES:
        if os.path.sep in cand or "/" in cand:
            if os.path.isfile(cand):
                return cand
        else:
            resolved = shutil.which(cand)
            if resolved:
                return resolved
    return None


def fetch_github_content(gh_path, repo, file_path):
    """GitHub Contents API 経由でファイル内容を取得(base64デコード済みテキストを返す)。
    失敗時は None。"""
    if not gh_path:
        return None
    import urllib.parse

    encoded = urllib.parse.quote(file_path)
    try:
        result = subprocess.run(
            [gh_path, "api", f"repos/{repo}/contents/{encoded}", "-q", ".content"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        raw = result.stdout.strip()
        decoded = base64.b64decode(raw, validate=False)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return None


def scan_project(project, gh_path):
    """1プロジェクトをスキャンし、結果 dict を返す。
    戻り値: {status, actual_model, detail, warn}
    status ∈ MATCH / MISMATCH / NOT_FOUND / SKIP / FETCH_FAILED
    """
    name = project["name"]
    local_path_raw = project.get("local_path")
    file_rel = project["file"]
    line = project.get("line")
    pattern = project.get("pattern")
    current_model = project.get("current_model", "unknown")
    allow_fixed_model = bool(project.get("allow_fixed_model", False))
    repo = project.get("repo", "unknown")

    regex = pattern_to_regex(pattern) if pattern else None

    if not local_path_raw:
        # GitHub 経由での確認(ローカルクローンが無いプロジェクト)
        display_path = f"(GitHub: {repo}) {file_rel}"
        content = fetch_github_content(gh_path, repo, file_rel)
        if content is None:
            return {
                "name": name,
                "display_path": display_path,
                "status": "FETCH_FAILED",
                "actual_model": None,
                "detail": "GitHub API から取得できませんでした",
                "warn": False,
            }
        if regex is None:
            return {
                "name": name,
                "display_path": display_path,
                "status": "NOT_FOUND",
                "actual_model": None,
                "detail": "pattern が gemini-versions.json に定義されていません",
                "warn": False,
            }
        match = regex.search(content)
        if not match:
            return {
                "name": name,
                "display_path": display_path,
                "status": "NOT_FOUND",
                "actual_model": None,
                "detail": "pattern がファイル内容と一致しません(検出パターン更新が必要な可能性)",
                "warn": False,
            }
        actual = match.group(1)
        status = "MATCH" if actual == current_model else "MISMATCH"
        warn = (not allow_fixed_model) and (not actual.endswith("-latest"))
        return {
            "name": name,
            "display_path": display_path,
            "status": status,
            "actual_model": actual,
            "detail": f"{file_rel}:{line}",
            "warn": warn,
        }

    # ローカルパス指定あり
    win_local = to_win_path(local_path_raw)
    display_path = f"{win_local}/{file_rel}"

    if not os.path.isdir(win_local):
        return {
            "name": name,
            "display_path": display_path,
            "status": "SKIP",
            "actual_model": None,
            "detail": f"ローカルパス無し(別マシン想定): {local_path_raw}",
            "warn": False,
        }

    full_path = os.path.join(win_local, *file_rel.split("/"))

    if not os.path.isfile(full_path):
        return {
            "name": name,
            "display_path": display_path,
            "status": "NOT_FOUND",
            "actual_model": None,
            "detail": "ファイルが見つかりません(local_path はあるが file が無い。移動/リネームの可能性)",
            "warn": False,
        }

    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return {
            "name": name,
            "display_path": display_path,
            "status": "NOT_FOUND",
            "actual_model": None,
            "detail": f"読み込みエラー: {e}",
            "warn": False,
        }

    if regex is None:
        return {
            "name": name,
            "display_path": display_path,
            "status": "NOT_FOUND",
            "actual_model": None,
            "detail": "pattern が gemini-versions.json に定義されていません",
            "warn": False,
        }

    match = regex.search(content)
    if not match:
        return {
            "name": name,
            "display_path": display_path,
            "status": "NOT_FOUND",
            "actual_model": None,
            "detail": "pattern がファイル内容と一致しません(検出パターン更新が必要な可能性)",
            "warn": False,
        }

    actual = match.group(1)
    status = "MATCH" if actual == current_model else "MISMATCH"
    warn = (not allow_fixed_model) and (not actual.endswith("-latest"))
    return {
        "name": name,
        "display_path": display_path,
        "status": status,
        "actual_model": actual,
        "detail": f"{file_rel}:{line}",
        "warn": warn,
    }


def main():
    args = sys.argv[1:]
    if not args:
        print("使い方: python scan_core.py <gemini-versions.json> [--update]")
        sys.exit(1)
    config_path = args[0]
    do_update = "--update" in args[1:]

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    projects = data["projects"]
    gh_path = find_gh()

    print("=" * 40)
    print(" Gemini API Version Scanner")
    print(f" 比較基準: 各プロジェクトの current_model (gemini-versions.json)")
    print(f" alias_status 実測: flash-latest={data.get('alias_status', {}).get('gemini-flash-latest', '?')}"
          f" / pro-latest={data.get('alias_status', {}).get('gemini-pro-latest', '?')}"
          f" (checked: {data.get('alias_status', {}).get('checked', '?')})")
    print("=" * 40)
    print()

    results = []
    for p in projects:
        r = scan_project(p, gh_path)
        r["current_model"] = p.get("current_model", "unknown")
        r["deploy"] = p.get("deploy", "")
        results.append(r)

        print(f"[{r['name']}] {r['display_path']}")
        status = r["status"]
        if status == "MATCH":
            print(f"  MATCH: {r['actual_model']}")
        elif status == "MISMATCH":
            print(f"  MISMATCH: 実コード={r['actual_model']} / レジストリ current_model={r['current_model']}")
        elif status == "NOT_FOUND":
            print(f"  NOT_FOUND: {r['detail']}")
        elif status == "SKIP":
            print(f"  SKIP: {r['detail']}")
        elif status == "FETCH_FAILED":
            print(f"  FETCH_FAILED: {r['detail']}")
        if r["warn"]:
            print(f"  WARN: -latest 以外の固定モデル指定です ({r['actual_model']})。"
                  f"-latest への統一対象か、意図的な固定(Vertex等)なら"
                  f" gemini-versions.json に allow_fixed_model: true を追加してください")

    counts = {"MATCH": 0, "MISMATCH": 0, "NOT_FOUND": 0, "SKIP": 0, "FETCH_FAILED": 0}
    warn_count = 0
    for r in results:
        counts[r["status"]] += 1
        if r["warn"]:
            warn_count += 1

    print()
    print("=" * 40)
    print(f" Results: MATCH={counts['MATCH']}  MISMATCH={counts['MISMATCH']}"
          f"  NOT_FOUND={counts['NOT_FOUND']}  SKIP={counts['SKIP']}"
          f"  FETCH_FAILED={counts['FETCH_FAILED']}  WARN={warn_count}")
    print("=" * 40)

    mismatches = [r for r in results if r["status"] == "MISMATCH"]
    if mismatches:
        print()
        print("--- レジストリと実コードが不一致のプロジェクト ---")
        for r in mismatches:
            print(f"  {r['name']}")
            print(f"    場所: {r['display_path']} ({r['detail']})")
            print(f"    実コード: {r['actual_model']} / レジストリ current_model: {r['current_model']}")
        print()

        if do_update:
            print("実コードをレジストリの current_model に合わせて更新しますか？"
                  " (y/N) ※このスクリプトはファイルを書き換えません。手動更新の手引きを表示するのみです")
            answer = input().strip().lower()
            if answer == "y":
                print("更新は各リポジトリで手動実行してください:")
                for r in mismatches:
                    print(f"  - {r['display_path']}: {r['actual_model']} → {r['current_model']} ({r['deploy']})")
                print()
                print("更新後、各プロジェクトで clasp push 等のデプロイを忘れずに実行してください。")
                print("(clasp push の前に git fetch を忘れずに — 別マシン更新の巻き戻し事故防止)")

    fetch_failed = [r for r in results if r["status"] == "FETCH_FAILED"]
    if fetch_failed:
        print()
        print("--- GitHub 取得に失敗したプロジェクト ---")
        for r in fetch_failed:
            print(f"  {r['name']}: {r['detail']}")

    not_found = [r for r in results if r["status"] == "NOT_FOUND"]
    if not_found:
        print()
        print("--- NOT_FOUND(検出できなかった)プロジェクト ---")
        for r in not_found:
            print(f"  {r['name']}: {r['detail']}")

    skipped = [r for r in results if r["status"] == "SKIP"]
    if skipped:
        print()
        print(f"NOTE: {len(skipped)} 件は別マシン想定のためこの端末ではスキップしました"
              f"(SKIP は異常ではありません):")
        for r in skipped:
            print(f"  {r['name']}: {r['detail']}")

    if counts["MISMATCH"] > 0 or counts["FETCH_FAILED"] > 0:
        sys.exit(1)
    if counts["NOT_FOUND"] > 0:
        print()
        print(f"WARNING: {counts['NOT_FOUND']} 件が NOT_FOUND のため「全一致」とは断定できません。"
              f"上の NOT_FOUND 一覧を確認してください。")
        sys.exit(2)

    print()
    print("全プロジェクトがレジストリ(current_model)と一致しています。")
    sys.exit(0)


if __name__ == "__main__":
    main()
