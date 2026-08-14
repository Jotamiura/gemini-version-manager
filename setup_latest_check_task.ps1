# setup_latest_check_task.ps1 — 毎朝 8:20 に -latest 切替チェックを走らせる Windows タスクを登録(初回1回だけ実行)
# case-hub の morning-refresh(8:25) と同じ建付け。ログオン中のみ動作(環境変数がユーザー単位のため)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "gemini-latest-switch-check"
$action = New-ScheduledTaskAction -Execute "pwsh.exe" `
  -Argument ("-NoProfile -WindowStyle Hidden -File `"{0}`"" -f (Join-Path $here "run_latest_check.ps1"))
$trigger = New-ScheduledTaskTrigger -Daily -At 08:20
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "登録完了: $taskName (毎日 8:20)"
