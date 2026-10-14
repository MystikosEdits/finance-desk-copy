# Restart the FinanceDesk task. Editing Python does NOTHING until this runs:
# the interpreter cached the old modules at import. The sidebar shows a
# STALE SERVER badge when a backend file is newer than the running process.
$ErrorActionPreference = "Continue"
cmd /c "schtasks /End /TN FinanceDesk >nul 2>&1"
Start-Sleep -Seconds 1
cmd /c "schtasks /Run /TN FinanceDesk >nul"
Start-Sleep -Seconds 4
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8020/health" -TimeoutSec 5
    Write-Host ("health: ok={0} setup_mode={1} stale={2}" -f $h.ok, $h.setup_mode, $h.stale) -ForegroundColor Green
} catch {
    Write-Host "not answering yet: $_" -ForegroundColor Red
    Write-Host "see data\logs\app.log"
}
