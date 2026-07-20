# build_repo.ps1 - Sestavi Kodi repozitar pro GitHub Pages (instalace z URL v Kodi)
# Pouziti: .\build_repo.ps1
#
# Vystup: docs/repo/  -> po pushi na GitHub zapni Pages (slozka /docs)
# URL repozitare: https://bicalordman.github.io/klempcinema/repo/

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$RepoBaseUrl = "https://bicalordman.github.io/klempcinema/repo"
$PluginId = "plugin.video.klempcinema"
$RepositoryId = "repository.klempcinema"
$Root = $PSScriptRoot
$Parent = Split-Path $Root -Parent
$DocsRepo = Join-Path $Root "docs\repo"

function New-KodiAddonZip {
    param(
        [string]$SrcRoot,
        [string]$AddonId,
        [string]$OutZip,
        [string[]]$ExcludeDir = @('__pycache__', '.git', '.cursor', 'tests', 'docs', 'repository.klempcinema'),
        [string[]]$ExcludeFile = @('*.zip', 'build_zip.ps1', 'build_repo.ps1', 'run_tests.ps1', 'fanart_old.jpg', 'icon_old.png')
    )

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

    $check = [System.IO.Compression.ZipFile]::OpenRead($OutZip)
    try {
        $dirs = @($check.Entries | Where-Object { $_.FullName.EndsWith('/') })
        $back = @($check.Entries | Where-Object { $_.FullName -match '\\' })
        $addon = $check.GetEntry("$AddonId/addon.xml")
        if ($dirs.Count -gt 0) { throw "ZIP $OutZip obsahuje slozkove zaznamy - Kodi to neumi" }
        if ($back.Count -gt 0) { throw "ZIP $OutZip obsahuje backslashe" }
        if (-not $addon) { throw "ZIP $OutZip neobsahuje $AddonId/addon.xml" }
    } finally {
        $check.Dispose()
    }
}

function Get-AddonXmlBody {
    param([string]$Path)
    $raw = Get-Content $Path -Raw -Encoding UTF8
    return ($raw -replace '^\s*<\?xml[^>]*\?>\s*', '').Trim()
}

# --- plugin zip ---
$pluginXml = [xml](Get-Content (Join-Path $Root "addon.xml") -Raw -Encoding UTF8)
$pluginVersion = $pluginXml.addon.version
$pluginZipName = "$PluginId-$pluginVersion.zip"
$pluginZipTemp = Join-Path $env:TEMP $pluginZipName

Write-Host "Sestavuji $pluginZipName ..."
New-KodiAddonZip -SrcRoot $Root -AddonId $PluginId -OutZip $pluginZipTemp

# --- repository zip ---
$repoXml = [xml](Get-Content (Join-Path $Root "$RepositoryId\addon.xml") -Raw -Encoding UTF8)
$repoVersion = $repoXml.addon.version
$repoZipName = "$RepositoryId-$repoVersion.zip"
$repoZipTemp = Join-Path $env:TEMP $repoZipName

Write-Host "Sestavuji $repoZipName ..."
New-KodiAddonZip -SrcRoot (Join-Path $Root $RepositoryId) -AddonId $RepositoryId -OutZip $repoZipTemp `
    -ExcludeDir @('__pycache__') -ExcludeFile @('*.zip')

# --- docs/repo/ ---
if (Test-Path $DocsRepo) { Remove-Item $DocsRepo -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $DocsRepo $PluginId) -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $DocsRepo $RepositoryId) -Force | Out-Null

Copy-Item $pluginZipTemp (Join-Path $DocsRepo "$PluginId\$pluginZipName") -Force
Copy-Item $repoZipTemp (Join-Path $DocsRepo "$RepositoryId\$repoZipName") -Force
Copy-Item $repoZipTemp (Join-Path $DocsRepo $repoZipName) -Force
Copy-Item $pluginZipTemp (Join-Path $Parent $pluginZipName) -Force

# Apache-style index.html — GitHub Pages neukazuje slozky, Kodi potrebuje odkazy
$repoIndex = @"
<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"><title>Index of /repo/</title></head>
<body>
<h1>Index of /repo/</h1>
<pre>
<a href="addons.xml">addons.xml</a>
<a href="addons.xml.md5">addons.xml.md5</a>
<a href="$repoZipName">$repoZipName</a>
<a href="$RepositoryId/">$RepositoryId/</a>
<a href="$PluginId/">$PluginId/</a>
</pre>
</body>
</html>
"@
[System.IO.File]::WriteAllText((Join-Path $DocsRepo "index.html"), $repoIndex, [System.Text.UTF8Encoding]::new($false))

$repoSubIndex = @"
<!DOCTYPE HTML>
<html>
<head><meta charset="UTF-8"><title>Index of /repo/$RepositoryId/</title></head>
<body>
<h1>Index of /repo/$RepositoryId/</h1>
<pre>
<a href="$repoZipName">$repoZipName</a>
</pre>
</body>
</html>
"@
[System.IO.File]::WriteAllText((Join-Path $DocsRepo "$RepositoryId\index.html"), $repoSubIndex, [System.Text.UTF8Encoding]::new($false))

# --- addons.xml (jen distributovatelne doplňky, ne samotny repository addon) ---
$pluginBody = Get-AddonXmlBody (Join-Path $Root "addon.xml")
$addonsXml = @"
<?xml version="1.0" encoding="UTF-8"?>
<addons>
$pluginBody
</addons>
"@

$addonsPath = Join-Path $DocsRepo "addons.xml"
[System.IO.File]::WriteAllText($addonsPath, $addonsXml, [System.Text.UTF8Encoding]::new($false))

$md5 = (Get-FileHash -Path $addonsPath -Algorithm MD5).Hash.ToLower()
[System.IO.File]::WriteAllText(
    (Join-Path $DocsRepo "addons.xml.md5"),
    $md5,
    [System.Text.UTF8Encoding]::new($false)
)

# GitHub Pages: vypnout Jekyll, jinak addons.xml nemusi fungovat
$nojekyll = Join-Path (Split-Path $DocsRepo -Parent) ".nojekyll"
if (-not (Test-Path $nojekyll)) {
    [System.IO.File]::WriteAllText($nojekyll, "", [System.Text.UTF8Encoding]::new($false))
}

Write-Host ""
Write-Host "OK: repozitar pripraven v docs\repo\"
Write-Host "  addons.xml     -> $RepoBaseUrl/addons.xml"
Write-Host "  datadir        -> $RepoBaseUrl/"
Write-Host "  plugin ZIP     -> $PluginId\$pluginZipName"
Write-Host "  repository ZIP -> $RepositoryId\$repoZipName"
Write-Host ""
Write-Host "Dalsi krok: push na GitHub + zapni Pages (Settings -> Pages -> Source: main /docs)"
Write-Host "V Kodi pridej zdroj: $RepoBaseUrl/"
