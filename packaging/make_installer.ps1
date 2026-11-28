# packaging/make_installer.ps1 — build the Windows installer (Inno Setup).
#
# Locates the Inno Setup compiler (ISCC); if it's absent on a clean windows-latest
# runner, installs Inno Setup 6 via Chocolatey (choco is preinstalled on the runner;
# the direct jrsoftware.org download stopped serving the binary and corrupts it).
# Then compiles packaging\installer.iss -> dist\OwnVoiceReader-Setup-x86_64.exe.
#
# Usage (PowerShell):  pwsh packaging\make_installer.ps1
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot          # repo root (packaging/ is one level down)
$iss  = Join-Path $PSScriptRoot "installer.iss"
$dist = Join-Path $repo "dist"

# 1) Find ISCC: the common install locations, then PATH.
$iscc = $null
$candidates = @(
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe",
  "C:\InnoSetup6\ISCC.exe"
)
foreach ($c in $candidates) {
  if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) {
  $cmd = Get-Command iscc -ErrorAction SilentlyContinue
  if ($cmd) { $iscc = $cmd.Source }
}

# 2) Install via Chocolatey if still missing (choco is preinstalled on windows-latest).
if (-not $iscc) {
  Write-Host "ISCC not found; installing Inno Setup 6 via Chocolatey ..."
  choco install innosetup --no-progress -y
  $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  if (-not (Test-Path $iscc)) {
    throw "Inno Setup installed but ISCC not found at $iscc"
  }
}

Write-Host "Compiling $iss with $iscc"
# ISCC writes its output where the .iss [Setup] OutputDir says (..\dist -> <repo>/dist).
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit $LASTEXITCODE)" }

$exe = Join-Path $dist "OwnVoiceReader-Setup-x86_64.exe"
if (-not (Test-Path $exe)) {
  throw "ISCC reported success but $exe was not produced"
}
Write-Host "done: $exe"
