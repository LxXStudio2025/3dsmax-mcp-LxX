[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$MaxDir = '',
    [string]$Python = '',
    [switch]$ConfigureCodex,
    [switch]$RebuildVenv
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts/python.exe'
if ($RebuildVenv -and (Test-Path -LiteralPath $venv)) {
    # Keep a recoverable copy; never recursively delete a user environment.
    Move-Item -LiteralPath $venv -Destination ($venv + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($Python) { & $Python -m venv $venv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $venv }
    else { & python -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment; specify -Python.' }
}
& $venvPython -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$installArgs = @((Join-Path $root 'tools/install.py'), '--install', '--lang', 'en,zh_CN')
if ($Version) { $installArgs += @('--version', $Version) }
if ($MaxDir) { $installArgs += @('--max-dir', $MaxDir) }
& $venvPython @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Max bridge installation failed.' }
if ($ConfigureCodex) {
    $configArgs = @((Join-Path $root 'tools/configure_codex.py'))
    if ($Version) { $configArgs += @('--version', $Version) }
    & $venvPython @configArgs
    if ($LASTEXITCODE -ne 0) { throw 'Codex configuration update failed.' }
}
Write-Output 'Setup complete. Start Max and refresh/restart the MCP connection.'
