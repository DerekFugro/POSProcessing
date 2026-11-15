# build.ps1 - make POSProcessing_Setup_<version>.exe
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Needs: the project .venv (run launch.bat once), git, Inno Setup 6 (winget install JRSoftware.InnoSetup),
#        tools\rtklib\rnx2rtkp.exe and the Trimble installers in installers\ (see installers\README.txt).
# Output: packaging\Output\POSProcessing_Setup_<version>.exe   - share inside Fugro only (Trimble software inside)
param([switch]$Test)   # -Test: build from uncommitted code (version gets "-modified"); never release that
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv - run launch.bat once first." }
foreach ($f in "tools\rtklib\rnx2rtkp.exe", "installers\TrimbleCFGUpdate.exe", "installers\convertToRinex_v4_0_1_10_sign.msi") {
    if (-not (Test-Path $f)) { throw "Missing $f - see installers\README.txt" }
}

# 1. version = date of the last commit + commit id; refuse uncommitted code
$dirty = git status --porcelain --untracked-files=no -- posproc gui app.py requirements.txt packaging
if ($dirty -and -not $Test) { throw "Commit your changes first - the installer must match a git commit.`n$dirty" }
$hash = (git log -1 --format=%h).Trim()
$date = (git log -1 --format=%cs).Trim()            # 2026-11-15
$num  = $date -replace '-', '.'                     # 2026.11.15
$version = "$num-$hash"
if ($dirty) { $version += "-modified" }
Set-Content -Path VERSION -Value "$version" -Encoding ascii -NoNewline
Write-Host "Version $version"

try {
    # native tools print warnings on stderr; Windows PowerShell must not treat that as a failure
    $ErrorActionPreference = "Continue"
    # 2. the app folder (no Python needed on the target PC)
    & $py -m pip install -q -r requirements.txt -r packaging\requirements-build.txt
    & $py -m PyInstaller --noconfirm --clean --distpath packaging\dist --workpath packaging\build packaging\POSProcessing.spec
    if ($LASTEXITCODE) { throw "PyInstaller failed" }

    # 3. Setup.exe
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 not found - winget install JRSoftware.InnoSetup" }
    & $iscc "/DAppVersion=$version" "/DAppNumber=$num" packaging\POSProcessing.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
    Get-ChildItem packaging\Output\*.exe | Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { Write-Host "Made $($_.FullName)" }
} finally {
    Remove-Item VERSION -ErrorAction SilentlyContinue   # the git checkout uses the commit, not a VERSION file
}
