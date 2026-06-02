<#
.SYNOPSIS
Prepare a local RetailOps backend development database.

.DESCRIPTION
Installs Python dependencies unless skipped, applies Django migrations, and runs
the bootstrap_local management command with optional sample data and external
Kiosk station provisioning. This script is for simple local setup only; use
start-retailops.ps1 for DB/storage startup profiles.

.PARAMETER SkipInstall
Skip pip upgrade and requirements installation.

.PARAMETER Seed
Load sample RetailOps data.

.PARAMETER ForceSeed
Clear and reload sample RetailOps data.

.PARAMETER ProvisionKiosk
Provision an external local Kiosk station and print its API key once.

.PARAMETER ResetPasswords
Reset demo user passwords to documented defaults.

.PARAMETER CreateVenv
Create/use .venv automatically.

.PARAMETER PythonCommand
Python executable to use. Defaults to python.

.PARAMETER Store
Kiosk store identifier. Defaults to DEV-LOCAL.

.PARAMETER Station
Kiosk station number. Defaults to 1.

.PARAMETER KioskLabel
Kiosk station label.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -Seed -ProvisionKiosk

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -CreateVenv -Seed
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$Seed,
    [switch]$ForceSeed,
    [switch]$ProvisionKiosk,
    [switch]$ResetPasswords,
    [switch]$CreateVenv,
    [string]$PythonCommand = "python",
    [string]$Store = "DEV-LOCAL",
    [ValidateRange(1, 2147483647)]
    [int]$Station = 1,
    [string]$KioskLabel = "Local development kiosk"
)

$ErrorActionPreference = "Stop"

function Test-ExecutableAvailable {
    param([string]$Command)

    if ([string]::IsNullOrWhiteSpace($Command)) {
        return $false
    }
    if (Get-Command $Command -ErrorAction SilentlyContinue) {
        return $true
    }
    return (Test-Path -LiteralPath $Command -PathType Leaf)
}

function Invoke-SetupCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($Arguments -join ' ') exited with code $LASTEXITCODE."
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

if (-not (Test-ExecutableAvailable -Command $PythonCommand)) {
    throw "Python command not found: $PythonCommand"
}

if ($CreateVenv) {
    Invoke-SetupCommand -FilePath $PythonCommand -Arguments @("-m", "venv", ".venv")
    $PythonCommand = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-ExecutableAvailable -Command $PythonCommand)) {
        throw "Virtual environment Python was not found at '$PythonCommand'."
    }
}

if (-not $SkipInstall) {
    Invoke-SetupCommand -FilePath $PythonCommand -Arguments @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-SetupCommand -FilePath $PythonCommand -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
}

Invoke-SetupCommand -FilePath $PythonCommand -Arguments @("manage.py", "migrate")

$bootstrapArgs = @("manage.py", "bootstrap_local")
if ($Seed) { $bootstrapArgs += "--seed" }
if ($ForceSeed) { $bootstrapArgs += "--force-seed" }
if ($ProvisionKiosk) { $bootstrapArgs += "--provision-kiosk" }
if ($ResetPasswords) { $bootstrapArgs += "--reset-passwords" }
$bootstrapArgs += @("--store", $Store, "--station", "$Station", "--kiosk-label", $KioskLabel)

Invoke-SetupCommand -FilePath $PythonCommand -Arguments $bootstrapArgs

Write-Host ""
Write-Host "RetailOps local backend is ready."
Write-Host "Start it with:"
Write-Host "  $PythonCommand manage.py runserver"
