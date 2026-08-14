# run_latest_check.ps1 — Task Scheduler から check_latest_switch.py を呼ぶラッパー(ログ付き)
# 登録は setup_latest_check_task.ps1 を初回1回だけ実行
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $here "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("latest-check-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$env:PYTHONIOENCODING = "utf-8"
"[{0}] start" -f (Get-Date -Format "HH:mm:ss") | Out-File -FilePath $log -Append -Encoding utf8
python (Join-Path $here "check_latest_switch.py") 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
"[{0}] end (exit={1})" -f (Get-Date -Format "HH:mm:ss"), $LASTEXITCODE | Out-File -FilePath $log -Append -Encoding utf8

# ログの30日ローテーション
Get-ChildItem $logDir -Filter "latest-check-*.log" |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
  Remove-Item -Force
