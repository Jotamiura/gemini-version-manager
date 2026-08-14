# -*- coding: utf-8 -*-
"""
check_latest_switch.py — Gemini -latest エイリアスの実体切替を検知し、
切替時だけスモークテストを回して Chatwork マイチャットへ通知する。

背景(2026-08-15 三浦さん要望「切り替わったとき動作チェックするテスト自動でやるフロー」):
  -latest 運用(全プロジェクト統一)では Google 側の自動切替で実体モデルが変わる。
  切替に気づかず OCR 精度が劣化するのが最大のリスクなので、
  「検知は自動、判断は人間」— 検知+スモーク+通知までを自動化し、
  各プロジェクトの dryRun 実行や採否判断は三浦さんに委ねる。

使い方:
  python check_latest_switch.py               # 通常実行(切替なしなら無音・exit 0)
  python check_latest_switch.py --dry-run     # Chatwork へ送らず通知本文を stdout へ
  python check_latest_switch.py --force-smoke # 切替がなくてもスモークを実行(動作確認用)

必要な環境変数:
  GEMINI_API_KEY            — Gemini API キー
  CHATWORK_API_TOKEN_MIURA  — 三浦さん本人トークン(マイチャット投稿用。無ければ stdout のみ)

状態ファイル: alias_state.json(前回観測した実体を記録。git 管理内)
※ 監視系の通知なので休業日カレンダー(SSOT)による停止は適用しない
  (Issue #15 ポリシー「エラー・障害監視通知は止めない」の枠)。
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "alias_state.json")
OCR_SAMPLE = os.path.join(HERE, "test_assets", "ocr_sample.png")
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
ALIASES = ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"]
MYCHAT_ROOM = "132868860"  # 三浦さんマイチャット


def call_gemini(model, parts, max_tokens=4096):
    # 3.7 Flash 以降は thinking トークンも maxOutputTokens を消費するため、
    # 予算が小さいと本文が空になる(2026-08-15 実測)。スモークは 4096 で余裕を持たせる
    """generateContent を1回叩いて (テキスト, modelVersion) を返す。失敗は例外。"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY が未設定")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    req = urllib.request.Request(
        f"{API_BASE}{model}:generateContent?key={key}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        d = json.load(res)
    text = ""
    for c in d.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            text += p.get("text", "")
    return text, d.get("modelVersion", "?")


def measure_aliases():
    """3エイリアスの実体を実測して {alias: 実体} を返す。"""
    out = {}
    for a in ALIASES:
        _, ver = call_gemini(a, [{"text": "hi"}], max_tokens=1)
        out[a] = ver
    return out


def smoke_structured(model):
    """スモーク1: JSON構造化出力が壊れていないか。"""
    prompt = (
        "次のテキストから JSON を抽出してください。"
        'キーは name, date, amount(数値) の3つ。JSON以外を出力しないこと。\n'
        "テキスト: 山田太郎様 2026年8月15日 ご請求金額 12,300円"
    )
    text, _ = call_gemini(model, [{"text": prompt}])
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        d = json.loads(raw.strip())
    except Exception as e:
        return False, f"JSONパース失敗: {e} / 出力: {text[:120]}"
    ok = d.get("name") == "山田太郎" and d.get("amount") == 12300 and "2026" in str(d.get("date", ""))
    return ok, f"抽出結果: {json.dumps(d, ensure_ascii=False)[:120]}"


def smoke_ocr(model):
    """スモーク2: 画像OCR + 先頭ゼロ(00395)が保持されるか。"""
    if not os.path.exists(OCR_SAMPLE):
        return None, "テスト画像なし(スキップ)"
    with open(OCR_SAMPLE, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    parts = [
        {"text": "画像に写っている数字列をそのまま出力してください。数字列以外は出力しないこと。"},
        {"inlineData": {"mimeType": "image/png", "data": b64}},
    ]
    text, _ = call_gemini(model, parts)
    got = text.strip()
    return got == "00395", f"期待 00395 / 実際 {got[:40]}"


def notify_chatwork(message, dry_run):
    if dry_run:
        print("---- [dry-run] Chatwork 通知本文 ----")
        print(message)
        return True
    token = os.environ.get("CHATWORK_API_TOKEN_MIURA")
    if not token:
        print("CHATWORK_API_TOKEN_MIURA 未設定のため通知スキップ(本文は以下)")
        print(message)
        return False
    req = urllib.request.Request(
        f"https://api.chatwork.com/v2/rooms/{MYCHAT_ROOM}/messages",
        data=urllib.parse.urlencode({"body": message}).encode(),
        headers={"X-ChatWorkToken": token},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status < 300


def main():
    dry_run = "--dry-run" in sys.argv
    force_smoke = "--force-smoke" in sys.argv

    current = measure_aliases()
    prev = {}
    if os.path.exists(STATE_PATH):
        prev = json.load(open(STATE_PATH, encoding="utf-8")).get("aliases", {})

    changed = {a: (prev.get(a), v) for a, v in current.items() if prev.get(a) and prev.get(a) != v}
    first_run = not prev

    for a, v in current.items():
        mark = " ★切替" if a in changed else ""
        print(f"{a} -> {v}{mark}")

    if not changed and not force_smoke:
        # 状態を保存して静かに終了(初回はベースライン記録)
        json.dump({"checked": __import__("datetime").date.today().isoformat(), "aliases": current},
                  open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("切替なし" + ("(初回: ベースライン記録)" if first_run else ""))
        return 0

    # スモークテスト(切替のあったエイリアス、force時は flash-latest)
    targets = list(changed.keys()) or ["gemini-flash-latest"]
    results = []
    for alias in targets:
        s_ok, s_detail = smoke_structured(alias)
        o_ok, o_detail = smoke_ocr(alias)
        results.append((alias, s_ok, s_detail, o_ok, o_detail))

    # 通知本文
    lines = ["[info][title]Gemini -latest 実体切替を検知[/title]"]
    for a, (old, new) in changed.items():
        lines.append(f"{a}: {old} → {new}")
    if force_smoke and not changed:
        lines.append("(--force-smoke による動作確認実行。切替はなし)")
    lines.append("")
    lines.append("■ スモークテスト(新実体)")
    all_ok = True
    for alias, s_ok, s_detail, o_ok, o_detail in results:
        s_mark = "OK" if s_ok else "NG"
        o_mark = "スキップ" if o_ok is None else ("OK" if o_ok else "NG")
        if s_ok is False or o_ok is False:
            all_ok = False
        lines.append(f"[{alias}]")
        lines.append(f"  構造化JSON: {s_mark} ({s_detail})")
        lines.append(f"  画像OCR(先頭ゼロ): {o_mark} ({o_detail})")
    lines.append("")
    if all_ok:
        lines.append("→ スモークは全通過。念のため主要プロジェクトの dryRun 実行を推奨")
    else:
        lines.append("⚠️ スモークNGあり。各プロジェクトの dryRun を回して確認してください")
    lines.append("(gemini-version-manager/check_latest_switch.py による自動検知)[/info]")
    message = "\n".join(lines)

    sent = notify_chatwork(message, dry_run)

    # 通知後に状態更新(dry-run では更新しない: 本番実行で改めて検知させる)
    if not dry_run:
        json.dump({"checked": __import__("datetime").date.today().isoformat(), "aliases": current},
                  open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("通知:", "送信済み" if sent else "未送信")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
