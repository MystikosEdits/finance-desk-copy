<#
.SYNOPSIS
  Deploy Finance Desk on this machine. Idempotent: run it after every pull.

  .\deploy.ps1            full: venv, pip, node, build, task, firewall, restart
  .\deploy.ps1 -NoRestart same, but leave the running task alone

WHAT IT DOES, AND WHY EACH STEP IS SHAPED THE WAY IT IS
  1. .venv        Reaction Desk pins numpy and friends in the GLOBAL site-packages
                  of the same interpreter. This app must neither bump nor be bumped
                  by them, so it gets its own venv. That is the one deliberate
                  departure from the ReactionDesk task, which runs py.exe directly.
  2. node         Build-time only. FastAPI serves frontend/dist at runtime, so Node
                  is never in the serving path. Installed from the official zip into
                  the user's profile - no admin, no MSI, no UAC prompt over SSH.
  3. build        dist/ is gitignored on purpose; app.py refuses to start without it,
                  so a half-finished deploy fails loud instead of serving a blank page.
  4. config.json  written WITHOUT a BOM ([IO.File]::WriteAllText). Out-File -Encoding
                  utf8 writes one and json.load chokes. That cost the pipeline a session.
  5. backup key   made if missing and printed ONCE. Put it in the password manager.
  6. firewall     TCP 8020, inbound, scoped to the tailnet (100.64.0.0/10). Reaction
                  Desk's rule is open to any address; bank data gets the tighter one.
  7. task         Register-ScheduledTask, not schtasks /Create: schtasks cannot set
                  ExecutionTimeLimit=0 (unlimited) or restart-on-failure, and a task
                  without the first is killed after 72 hours.
#>
param([switch]$NoRestart)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$TaskName   = "FinanceDesk"
$Port       = 8020
$NodeVer    = "v24.19.0"
$NodeDir    = Join-Path $env:LOCALAPPDATA "Programs\node-$NodeVer"
$PyGlobal   = "python"

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

# 1. venv --------------------------------------------------------------------
Step "python venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $PyGlobal -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $Py --version

# 2. node -------------------------------------------------------------------
Step "node"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node -and (Test-Path (Join-Path $NodeDir "node.exe"))) {
    $env:Path = "$NodeDir;$env:Path"
    $node = Get-Command node -ErrorAction SilentlyContinue
}
if (-not $node) {
    $zip = Join-Path $env:TEMP "node-$NodeVer-win-x64.zip"
    $url = "https://nodejs.org/dist/$NodeVer/node-$NodeVer-win-x64.zip"
    Write-Host "downloading $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $tmp = Join-Path $env:TEMP "node-extract"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $zip -DestinationPath $tmp
    New-Item -ItemType Directory -Force (Split-Path $NodeDir) | Out-Null
    Move-Item (Join-Path $tmp "node-$NodeVer-win-x64") $NodeDir
    Remove-Item $zip -Force
    # Persist for future shells (user scope, no admin needed).
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$NodeDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$NodeDir;$userPath", "User")
    }
    $env:Path = "$NodeDir;$env:Path"
}
node --version
npm --version

# 3. build ------------------------------------------------------------------
Step "frontend build"
Push-Location frontend
try {
    if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
if (-not (Test-Path "frontend\dist\index.html")) { throw "build produced no dist/index.html" }

# 4. config + data ----------------------------------------------------------
Step "config and data"
if (-not (Test-Path "config.json")) {
    $cfg = @'
{
  "host": "0.0.0.0",
  "port": 8020,
  "auth_enabled": true,
  "sync_times": ["06:30", "14:30", "22:30"],
  "prices_time": "04:00",
  "backup_time": "03:30",
  "onedrive_backup_dir": "",
  "receipt_required_over_cents": 7500
}
'@
    [IO.File]::WriteAllText((Join-Path $Root "config.json"), $cfg)   # no BOM
}
New-Item -ItemType Directory -Force "data" | Out-Null   # migrations run at app start

# 5. backup key -------------------------------------------------------------
Step "backup key"
& $Py backend\backup.py ensure-key

# 6. firewall ---------------------------------------------------------------
Step "firewall"
$ruleName = "Finance Desk $Port"
$rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -RemoteAddress 100.64.0.0/10 -Profile Any | Out-Null
    Write-Host "created firewall rule '$ruleName' (tailnet only)"
} else {
    Write-Host "firewall rule '$ruleName' exists"
}

# 7. scheduled task ---------------------------------------------------------
Step "scheduled task $TaskName"
# Over SSH, $env:USERDOMAIN says WORKGROUP, which maps to no account. The
# computer name is what the local account actually lives under.
$User     = "$env:COMPUTERNAME\$env:USERNAME"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$action   = New-ScheduledTaskAction -Execute $Py -Argument "backend\app.py" -WorkingDirectory $Root
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $User
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "updated task"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "registered task"
}

# 8. (re)start --------------------------------------------------------------
if (-not $NoRestart) {
    Step "restart"
    # cmd swallows the "not running" error; under Stop, a native stderr line would abort the script.
    cmd /c "schtasks /End /TN $TaskName >nul 2>&1"
    Start-Sleep -Seconds 1
    cmd /c "schtasks /Run /TN $TaskName >nul"
    if ($LASTEXITCODE -ne 0) { throw "schtasks /Run failed" }
    Start-Sleep -Seconds 4
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
        Write-Host ("health: ok={0} dist_built={1} setup_mode={2}" -f $h.ok, $h.dist_built, $h.setup_mode) -ForegroundColor Green
        if ($h.setup_mode) {
            Write-Host "SETUP MODE: bound to 127.0.0.1 until the first account exists." -ForegroundColor Yellow
            Write-Host "  from the laptop:  ssh -L 8020:127.0.0.1:8020 you@your-server" -ForegroundColor Yellow
            Write-Host "  then open         http://localhost:8020/setup   and run .\restart.ps1 afterwards" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "health check failed: $_" -ForegroundColor Red
        Write-Host "look at data\logs\app.log"
    }
}
Write-Host ""
Write-Host "done." -ForegroundColor Green
