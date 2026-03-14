$ErrorActionPreference = "Stop"

$Package = "mcp-harbour"
$TaskName = "MCP Harbour Daemon"

function Info($msg)  { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

# ── 1. Install package ─────────────────────────────────────────────

$uv = Get-Command uv -ErrorAction SilentlyContinue
$pipx = Get-Command pipx -ErrorAction SilentlyContinue

$Repo = "https://github.com/GPARS-org/mcp-harbour.git"

if ($uv) {
    Info "Installing $Package via uv..."
    & uv tool install $Package 2>$null
    if ($LASTEXITCODE -ne 0) { & uv tool install "git+$Repo" }
} elseif ($pipx) {
    Info "Installing $Package via pipx..."
    & pipx install $Package 2>$null
    if ($LASTEXITCODE -ne 0) { & pipx install "git+$Repo" }
} else {
    Fail "Neither uv nor pipx found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
}

# Verify
$harbourCmd = Get-Command harbour -ErrorAction SilentlyContinue
if (-not $harbourCmd) {
    Fail "'harbour' command not found after install. Ensure your Python scripts directory is in PATH."
}

$HarbourBin = $harbourCmd.Source
Info "Installed harbour at $HarbourBin"

# ── 2. Register scheduled task ─────────────────────────────────────

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Warn "Removing existing scheduled task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$logDir = Join-Path $env:APPDATA "mcp-harbour"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$action = New-ScheduledTaskAction `
    -Execute $HarbourBin `
    -Argument "serve" `
    -WorkingDirectory $logDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Seconds 10) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "MCP Harbour security enforcement daemon" | Out-Null

# Start the task now
Start-ScheduledTask -TaskName $TaskName

Info "Registered scheduled task: $TaskName"
Info "Daemon started on 127.0.0.1:4767"

Write-Host ""
Info "Manage with:"
Write-Host "  harbour status"
Write-Host "  harbour stop"
Write-Host "  harbour start"

Write-Host ""
Info "Installation complete."
