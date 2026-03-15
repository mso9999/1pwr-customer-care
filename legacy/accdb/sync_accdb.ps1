<#
.SYNOPSIS
    Sync the ACCDB from the production EC2 to the CC backend clone.

.DESCRIPTION
    Stops the CC API service, copies the live ACCDB from the production
    server, removes stale lock files, restarts the service, and optionally
    re-runs the meter readings import to refresh tblmonthlyconsumption.

    Designed to run as a Windows Scheduled Task (e.g., nightly at 2 AM).

.NOTES
    BEFORE FIRST USE:
    1. Set $ProductionSource below to the UNC path or local path of the live ACCDB.
    2. Ensure network access from this EC2 to the production server (SMB/445 or mapped drive).
    3. Test manually: powershell -ExecutionPolicy Bypass -File C:\acdb-customer-api\sync_accdb.ps1
    4. Register as scheduled task:
       schtasks /Create /TN "ACDBSync" /TR "powershell -ExecutionPolicy Bypass -File C:\acdb-customer-api\sync_accdb.ps1" /SC DAILY /ST 02:00 /RU SYSTEM

    To trigger manually:
       schtasks /Run /TN "ACDBSync"
#>

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION — Edit these values for your environment
# ═══════════════════════════════════════════════════════════════════════════

# Source: UNC path to the live ACCDB on the production EC2
# Examples:
#   \\10.0.1.50\AccessDB\tuacc.accdb          (SMB share)
#   \\production-ec2\C$\path\to\tuacc.accdb    (admin share, needs credentials)
$ProductionSource = ""

# Destination: local clone directory on this EC2
$CloneDir = "C:\Users\Administrator\Desktop\AccessDB_Clone"

# Service name (the scheduled task that runs the CC API)
$ServiceTask = "ACDBCustomerAPI"

# Python venv for optional post-sync import refresh
$PythonExe = "C:\acdb-customer-api\venv\Scripts\python.exe"
$ImportScript = "C:\acdb-customer-api\import_meter_readings.py"

# Whether to re-run the meter readings import after sync (refreshes tblmonthlyconsumption)
$RunImportAfterSync = $true

# Log file
$LogFile = "C:\acdb-customer-api\logs\sync_accdb.log"

# ═══════════════════════════════════════════════════════════════════════════
# SCRIPT — Do not edit below unless you know what you're doing
# ═══════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $Message"
    Write-Host $line
    # Ensure log directory exists
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

# ── Preflight checks ──

Write-Log "═══════════════════════════════════════════════════════"
Write-Log "ACCDB SYNC START"
Write-Log "═══════════════════════════════════════════════════════"

if (-not $ProductionSource) {
    Write-Log "ERROR: `$ProductionSource is not configured. Edit sync_accdb.ps1 and set the UNC path."
    exit 1
}

if (-not (Test-Path $ProductionSource)) {
    Write-Log "ERROR: Cannot access production ACCDB at: $ProductionSource"
    Write-Log "  Check network connectivity and SMB share permissions."
    exit 1
}

$sourceFile = Get-Item $ProductionSource
Write-Log "Source: $ProductionSource ($([math]::Round($sourceFile.Length / 1MB, 1)) MB, modified $($sourceFile.LastWriteTime))"

# Find the current clone file
$cloneFiles = Get-ChildItem -Path $CloneDir -Filter "*.accdb" -ErrorAction SilentlyContinue
if ($cloneFiles.Count -eq 0) {
    Write-Log "WARNING: No .accdb file found in $CloneDir — will create a fresh copy"
    $cloneFile = Join-Path $CloneDir $sourceFile.Name
} else {
    $cloneFile = $cloneFiles[0].FullName
    Write-Log "Clone:  $cloneFile ($([math]::Round($cloneFiles[0].Length / 1MB, 1)) MB, modified $($cloneFiles[0].LastWriteTime))"
}

# ── Step 1: Stop the CC API service ──

Write-Log "Stopping $ServiceTask..."
schtasks.exe /End /TN $ServiceTask 2>$null
$global:LASTEXITCODE = 0
Get-Process -Name python* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

# Remove stale lock files
Get-ChildItem -Path $CloneDir -Filter "*.ldb" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    Write-Log "Removed lock file: $($_.Name)"
}

Write-Log "Service stopped"

# ── Step 2: Copy the ACCDB ──

Write-Log "Copying ACCDB..."
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

try {
    Copy-Item -Path $ProductionSource -Destination $cloneFile -Force
    $stopwatch.Stop()
    $newSize = (Get-Item $cloneFile).Length
    Write-Log "Copy complete: $([math]::Round($newSize / 1MB, 1)) MB in $([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s"
} catch {
    $stopwatch.Stop()
    Write-Log "ERROR: Copy failed after $([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s — $_"
    # Try to restart the service even if copy failed
    Write-Log "Restarting service with old database..."
    schtasks.exe /Run /TN $ServiceTask
    exit 1
}

# ── Step 3: Restart the CC API service ──

Write-Log "Starting $ServiceTask..."
schtasks.exe /Run /TN $ServiceTask
$global:LASTEXITCODE = 0
Start-Sleep -Seconds 8

# Health check
$healthy = $false
for ($i = 1; $i -le 3; $i++) {
    try {
        $response = Invoke-RestMethod -Uri http://localhost:8100/health -TimeoutSec 10
        Write-Log "Health check passed: status=$($response.status)"
        $healthy = $true
        break
    } catch {
        Write-Log "Health check attempt $i/3 failed: $_"
        Start-Sleep -Seconds 5
    }
}

if (-not $healthy) {
    Write-Log "WARNING: Service may not be healthy after sync"
}

# ── Step 4: Optional — re-run meter readings import ──

if ($RunImportAfterSync -and $healthy) {
    if ((Test-Path $PythonExe) -and (Test-Path $ImportScript)) {
        Write-Log "Running meter readings import (--local-only)..."
        try {
            $importOutput = & $PythonExe $ImportScript --local-only 2>&1
            $importOutput | ForEach-Object { Write-Log "  [import] $_" }
            Write-Log "Import complete"
        } catch {
            Write-Log "WARNING: Import failed — $_"
        }
    } else {
        Write-Log "Skipping import (python or script not found)"
    }
}

Write-Log "═══════════════════════════════════════════════════════"
Write-Log "ACCDB SYNC COMPLETE"
Write-Log "═══════════════════════════════════════════════════════"
