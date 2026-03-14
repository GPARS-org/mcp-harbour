$ErrorActionPreference = "Stop"

$Package = "mcp-harbour"
$TaskName = "MCP Harbour Daemon"

function Info($msg) { Write-Host "[+] $msg" -ForegroundColor Green }

# ── 1. Stop and remove scheduled task ──────────────────────────────

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Info "Removed scheduled task."
}

# ── 2. Uninstall package ──────────────────────────────────────────

$uv = Get-Command uv -ErrorAction SilentlyContinue
$pipx = Get-Command pipx -ErrorAction SilentlyContinue

if ($uv) {
    Info "Uninstalling $Package via uv..."
    & uv tool uninstall $Package 2>$null
} elseif ($pipx) {
    Info "Uninstalling $Package via pipx..."
    & pipx uninstall $Package 2>$null
}

Info "Uninstall complete."
Info "Config files remain at $env:APPDATA\mcp-harbour — delete manually if desired."
