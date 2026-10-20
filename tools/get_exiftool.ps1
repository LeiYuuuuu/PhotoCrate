# 下载最新版 ExifTool（Windows 64 位）到 tools\exiftool\exiftool.exe
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"

$dest = Join-Path $PSScriptRoot "exiftool"
$tmp = Join-Path $env:TEMP ("exiftool_dl_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    $ver = (Invoke-WebRequest "https://exiftool.org/ver.txt" -UseBasicParsing).Content.Trim()
    Write-Host "最新版本：$ver"
    $zipName = "exiftool-$($ver)_64.zip"
    $zip = Join-Path $tmp $zipName
    $urls = @("https://exiftool.org/$zipName", "https://sourceforge.net/projects/exiftool/files/$zipName/download")
    $ok = $false
    foreach ($u in $urls) {
        try {
            Write-Host "下载 $u"
            Invoke-WebRequest $u -OutFile $zip -UseBasicParsing -UserAgent "Wget"
            if ((Get-Item $zip).Length -gt 1MB) { $ok = $true; break }
        } catch { Write-Host "  失败：$($_.Exception.Message)" }
    }
    if (-not $ok) { throw "无法下载 ExifTool" }

    $unz = Join-Path $tmp "unz"
    Expand-Archive -Path $zip -DestinationPath $unz -Force
    $exe = Get-ChildItem -Path $unz -Recurse -Filter "exiftool*.exe" | Select-Object -First 1
    if (-not $exe) { throw "压缩包中没有找到 exiftool.exe" }
    $srcDir = $exe.DirectoryName

    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Path (Join-Path $srcDir "*") -Destination $dest -Recurse -Force
    # Windows 版文件名是 exiftool(-k).exe，改名后才不会在结束时等待按键
    Get-ChildItem -Path $dest -Filter "exiftool*.exe" | ForEach-Object {
        if ($_.Name -ne "exiftool.exe") { Rename-Item $_.FullName "exiftool.exe" -Force }
    }
    $v = & (Join-Path $dest "exiftool.exe") -ver
    Write-Host "ExifTool $v 已就绪：$dest" -ForegroundColor Green
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
