# run_tests.ps1 — unit testy bez Kodi (nemení chovani addonu)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$py = $null
foreach ($cmd in @("python", "py", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $py = $cmd
        break
    }
}
if (-not $py) {
    Write-Error "Python nenalezen. Nainstaluj Python 3 a spust znovu."
}

if ($py -eq "py") {
    & py -3 -m unittest discover -s tests -p "test_*.py" -v
} else {
    & $py -m unittest discover -s tests -p "test_*.py" -v
}

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "OK: vsechny testy prosly"
