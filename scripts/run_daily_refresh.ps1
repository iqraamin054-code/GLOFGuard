$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.python\python.exe'
$LogDirectory = Join-Path $ProjectRoot 'data\logs'
$LogPath = Join-Path $LogDirectory ("refresh-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

New-Item -ItemType Directory -Force $LogDirectory | Out-Null
Push-Location $ProjectRoot
try {
    & $Python -m glofguard.cli refresh *>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
        throw "GLOF refresh failed with exit code $LASTEXITCODE"
    }
    & $Python -m glofguard.cli monitor *>&1 | Tee-Object -FilePath $LogPath -Append
    if ($LASTEXITCODE -ne 0) {
        throw "GLOF monitoring failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
