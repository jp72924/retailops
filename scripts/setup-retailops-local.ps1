<#
.SYNOPSIS
Prepare a local RetailOps backend development database.

.DESCRIPTION
Installs Python dependencies unless skipped, applies Django migrations, and runs
the public init command. Operational setup is the default; pass -Demo for
documented demo users and optional sample data. This script is for simple local
setup only; use start-retailops.ps1 for DB/storage startup profiles.

.PARAMETER SkipInstall
Skip pip upgrade and requirements installation.

.PARAMETER Seed
Load sample RetailOps data. Requires -Demo.

.PARAMETER ForceSeed
Clear and reload sample RetailOps data. Requires -Demo.

.PARAMETER ProvisionKiosk
Provision a demo Kiosk station and print its API key once. Requires -Demo.

.PARAMETER ResetPasswords
Reset demo user passwords to documented defaults.

.PARAMETER CreateVenv
Create/use .venv automatically.

.PARAMETER Demo
Use documented demo users and optional sample data.

.PARAMETER Operational
Accepted for compatibility. Operational setup is now the default.

.PARAMETER Yes
Confirm init without an interactive prompt.

.PARAMETER NoInput
Disable interactive prompts for init.

.PARAMETER AdminEmail
Email for the first operational admin user.

.PARAMETER AdminFirstName
First operational admin first name.

.PARAMETER AdminLastName
First operational admin last name.

.PARAMETER AdminPasswordEnv
Environment variable containing the initial admin password.

.PARAMETER PythonCommand
Python executable to use. Defaults to python.

.PARAMETER Store
Kiosk store identifier. Defaults to DEV-LOCAL.

.PARAMETER Station
Kiosk station number. Defaults to 1.

.PARAMETER StationCount
Number of Kiosk stations to create during operational setup.

.PARAMETER KioskLabel
Kiosk station label.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -AdminEmail owner@example.com

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -CreateVenv -AdminEmail owner@example.com

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\setup-retailops-local.ps1 -Demo -Seed -ProvisionKiosk
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$Seed,
    [switch]$ForceSeed,
    [switch]$ProvisionKiosk,
    [switch]$ResetPasswords,
    [switch]$CreateVenv,
    [switch]$Demo,
    [switch]$Operational,
    [switch]$Yes,
    [switch]$NoInput,
    [string]$AdminEmail = "",
    [string]$AdminFirstName = "Store",
    [string]$AdminLastName = "Owner",
    [string]$AdminPasswordEnv = "RETAILOPS_INITIAL_ADMIN_PASSWORD",
    [string]$PythonCommand = "python",
    [string]$Store = "DEV-LOCAL",
    [ValidateRange(1, 2147483647)]
    [int]$Station = 1,
    [ValidateRange(0, 2147483647)]
    [int]$StationCount = 0,
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

if ($Demo) {
    $initArgs = @("manage.py", "init", "--demo")
    if ($Seed) { $initArgs += "--seed" }
    if ($ForceSeed) { $initArgs += "--force-seed" }
    if ($ProvisionKiosk) { $initArgs += "--provision-kiosk" }
    if ($ResetPasswords) { $initArgs += "--reset-passwords" }
    $initArgs += @("--store", $Store, "--station", "$Station", "--kiosk-label", $KioskLabel)
    Invoke-SetupCommand -FilePath $PythonCommand -Arguments $initArgs
}
else {
    if ($Seed -or $ForceSeed -or $ResetPasswords) {
        throw "-Seed, -ForceSeed, and -ResetPasswords require -Demo."
    }
    if ($ProvisionKiosk) {
        throw "-ProvisionKiosk requires -Demo. For operational setup, use -StationCount."
    }

    $initArgs = @("manage.py", "init")
    if (-not [string]::IsNullOrWhiteSpace($AdminEmail)) {
        $initArgs += @("--admin-email", $AdminEmail)
    }
    if (-not [string]::IsNullOrWhiteSpace($AdminFirstName)) {
        $initArgs += @("--admin-first-name", $AdminFirstName)
    }
    if (-not [string]::IsNullOrWhiteSpace($AdminLastName)) {
        $initArgs += @("--admin-last-name", $AdminLastName)
    }
    if (-not [string]::IsNullOrWhiteSpace($AdminPasswordEnv)) {
        $initArgs += @("--admin-password-env", $AdminPasswordEnv)
    }
    if ($Yes) { $initArgs += "--yes" }
    if ($NoInput) { $initArgs += "--no-input" }
    if ($ProvisionKiosk -or $StationCount -gt 0) {
        $count = $StationCount
        if ($count -eq 0) { $count = 1 }
        $initArgs += @(
            "--store", $Store,
            "--station-start", "$Station",
            "--station-count", "$count",
            "--kiosk-label-prefix", $KioskLabel
        )
    }
    Invoke-SetupCommand -FilePath $PythonCommand -Arguments $initArgs
}

Write-Host ""
Write-Host "RetailOps local backend is ready."
Write-Host "Start it with:"
Write-Host "  $PythonCommand manage.py runserver"
