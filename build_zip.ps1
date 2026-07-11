# build_zip.ps1 - Kodi-kompatibilni ZIP (jen soubory, cesty s /, bez slozek v archivu)
# Fungujici stare ZIPy (0.0.83) nemaji zaznamy typu plugin.../resources/ - tar.exe je pridava a Kodi pada.
# Pouziti: .\build_zip.ps1

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$AddonId = "plugin.video.klempcinema"
$SrcRoot = $PSScriptRoot
$Parent = Split-Path $SrcRoot -Parent

$xml = [xml](Get-Content (Join-Path $SrcRoot "addon.xml") -Raw -Encoding UTF8)
$Version = $xml.addon.version
$OutZip = Join-Path $Parent "$AddonId-$Version.zip"

$ExcludeDir = @('__pycache__', '.git', '.cursor', 'tests', 'docs', 'repository.klempcinema')
$ExcludeFile = @('*.zip', 'build_zip.ps1', 'build_repo.ps1', 'run_tests.ps1', 'fanart_old.jpg', 'icon_old.png')

if (Test-Path $OutZip) { Remove-Item $OutZip -Force }

$zip = [System.IO.Compression.ZipFile]::Open(
    $OutZip,
    [System.IO.Compression.ZipArchiveMode]::Create
)

try {
    Get-ChildItem $SrcRoot -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($SrcRoot.Length).TrimStart('\', '/')
        foreach ($part in $rel.Split('\')) {
            if ($ExcludeDir -contains $part) { return }
        }
        foreach ($pat in $ExcludeFile) {
            if ($_.Name -like $pat) { return }
        }

        $entryName = ($AddonId + '/' + ($rel -replace '\\', '/'))
        $entry = $zip.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
        $inStream = [System.IO.File]::OpenRead($_.FullName)
        try {
            $outStream = $entry.Open()
            try { $inStream.CopyTo($outStream) } finally { $outStream.Dispose() }
        } finally { $inStream.Dispose() }
    }
} finally {
    $zip.Dispose()
}

$size = (Get-Item $OutZip).Length
Write-Host "OK: $OutZip ($size bytes)"

$check = [System.IO.Compression.ZipFile]::OpenRead($OutZip)
try {
    $dirs = @($check.Entries | Where-Object { $_.FullName.EndsWith('/') })
    $back = @($check.Entries | Where-Object { $_.FullName -match '\\' })
    $addon = $check.GetEntry("$AddonId/addon.xml")
    if ($dirs.Count -gt 0) { throw "ZIP obsahuje $($dirs.Count) slozkovych zaznamu - Kodi to neumí" }
    if ($back.Count -gt 0) { throw "ZIP obsahuje backslashe" }
    if (-not $addon) { throw "Chybi $AddonId/addon.xml" }
    Write-Host "Kontrola: $($check.Entries.Count) souboru, 0 slozek, addon.xml OK"
} finally {
    $check.Dispose()
}
