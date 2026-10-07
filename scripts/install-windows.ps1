<#
.SYNOPSIS
    Set up openrct2-mcp on Windows for Claude Code.

.DESCRIPTION
    Creates .venv, installs the MCP server, installs the openrct2-bridge and
    ride-builder plugins, enables plugin hot reloading in OpenRCT2's config.ini,
    and writes .mcp.json so Claude Code starts the server. Safe to re-run.
    Close OpenRCT2 first: the game rewrites config.ini when it exits.

.PARAMETER OpenRCT2Path
    OpenRCT2 install folder, or the path to openrct2.exe / openrct2.com.
    Defaults to $env:PYRCT2_OPENRCT2_PATH, then "C:\Program Files\OpenRCT2".

.PARAMETER Python
    Python 3.11+ interpreter used to create .venv. Defaults to python on PATH,
    then the py launcher.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -OpenRCT2Path "D:\Games\OpenRCT2"
#>
[CmdletBinding()]
param(
    [string]$OpenRCT2Path,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Resolve-OpenRCT2Com {
    if ($OpenRCT2Path) {
        $candidates = @($OpenRCT2Path)
    } else {
        $candidates = @($env:PYRCT2_OPENRCT2_PATH, (Join-Path $env:ProgramFiles 'OpenRCT2')) | Where-Object { $_ }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        $item = Get-Item -LiteralPath $candidate
        $dir = if ($item.PSIsContainer) { $item.FullName } else { $item.DirectoryName }
        # pyrct2 reads the game's console output, which only openrct2.com provides.
        $com = Join-Path $dir 'openrct2.com'
        if (Test-Path -LiteralPath $com) { return $com }
    }
    throw "Could not find openrct2.com. Pass -OpenRCT2Path with your OpenRCT2 install folder."
}

function Resolve-PythonCommand {
    $candidates = New-Object System.Collections.Generic.List[object]
    if ($Python) { $candidates.Add(@($Python)) }
    # Prefer python.org installs over the Microsoft Store build, which sandboxes AppData writes.
    Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        ForEach-Object { $candidates.Add(@($_.Source)) }
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates.Add(@('py', '-3')) }

    foreach ($command in $candidates) {
        $exe = $command[0]
        $rest = @($command | Select-Object -Skip 1)
        try {
            $info = & $exe @rest -c 'import sys; print(sys.version_info >= (3, 11)); print(sys.executable)' 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -eq 0 -and $info -and $info[0] -eq 'True') {
            if ($info[1] -like '*\WindowsApps\*') {
                Write-Warning "Using the Microsoft Store Python ($($info[1])). A python.org install is recommended."
            }
            return ,$command
        }
    }
    throw "Python 3.11+ not found. Install it from python.org or pass -Python C:\path\to\python.exe."
}

if (Get-Process -Name openrct2 -ErrorAction SilentlyContinue) {
    throw "OpenRCT2 is running. Close it first: the game rewrites config.ini when it exits."
}

$openrct2Com = Resolve-OpenRCT2Com
Write-Step "OpenRCT2: $openrct2Com"

Push-Location $Root
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $pythonCommand = Resolve-PythonCommand
        Write-Step "Creating .venv with $($pythonCommand -join ' ')"
        $pythonArgs = @($pythonCommand | Select-Object -Skip 1) + @('-m', 'venv', $Venv)
        Invoke-Native $pythonCommand[0] $pythonArgs
    } else {
        Write-Step "Reusing existing .venv"
    }

    Write-Step "Installing openrct2-mcp and dependencies"
    Invoke-Native $VenvPython @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-e', '.[dev]')

    Write-Step "Installing the openrct2-bridge plugin (pyrct2 setup)"
    $env:PYRCT2_OPENRCT2_PATH = $openrct2Com
    Invoke-Native (Join-Path $Venv 'Scripts\pyrct2.exe') @('setup')

    Write-Step "Installing ride-builder, enabling hot reload, writing .mcp.json"
    Invoke-Native $VenvPython @('-m', 'openrct2_mcp.install')
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  1. Launch OpenRCT2 and load a park."
Write-Host "  2. Check the plugins: .venv\Scripts\python.exe scripts\check_connection.py"
Write-Host "  3. Start Claude Code in this folder (claude), approve the 'openrct2' MCP server, and run /mcp."
