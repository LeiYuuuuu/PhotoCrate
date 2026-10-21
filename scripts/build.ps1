# 把影匣打包成 Windows 程序（文件夹形式，杀毒误报少），可选再生成安装包。
#   默认：PyInstaller（无需 C 编译器，最省心）
#   -Tool nuitka：Nuitka 编译（启动更快；需 Python 3.12 及以下，会自动下载 MinGW 编译器）
param([ValidateSet("pyinstaller", "nuitka")][string]$Tool = "pyinstaller")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

function Pause-Exit($code) { Write-Host ""; Read-Host "按回车键退出"; exit $code }

if (-not (Test-Path $py)) {
    Write-Host "还没有运行环境，先执行一次 run.bat（会自动安装依赖）后再打包。" -ForegroundColor Yellow
    Pause-Exit 1
}
if (-not (Test-Path "tools\exiftool\exiftool.exe")) {
    Write-Host "下载 ExifTool…" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File "tools\get_exiftool.ps1"
}

if ($Tool -eq "pyinstaller") {
    Write-Host "[1/3] 安装 PyInstaller…" -ForegroundColor Cyan
    & $py -m pip install -q --disable-pip-version-check "pyinstaller>=6.6"
    Write-Host "[2/3] 打包中（约 2~5 分钟）…" -ForegroundColor Cyan
    & $py -m PyInstaller --noconfirm --clean --windowed `
        --name PhotoCrate --icon assets\app.ico `
        --add-data "data;data" --add-data "assets;assets" --add-data "plugins_examples;plugins_examples" `
        --collect-data GeoToolCN --collect-all pillow_heif --collect-all rawpy `
        --hidden-import exiftool --hidden-import send2trash --collect-all onnxruntime `
        launcher.py
    if ($LASTEXITCODE -ne 0) { Write-Host "打包失败" -ForegroundColor Red; Pause-Exit 1 }
    $dist = Join-Path $root "dist\PhotoCrate"
} else {
    Write-Host "[1/3] 安装 Nuitka…" -ForegroundColor Cyan
    & $py -m pip install -q --disable-pip-version-check "nuitka>=2.4" ordered-set zstandard
    Write-Host "[2/3] 编译中（首次约 10~20 分钟）…" -ForegroundColor Cyan
    & $py -m nuitka --standalone --assume-yes-for-downloads --enable-plugin=pyside6 `
        --windows-console-mode=disable --windows-icon-from-ico=assets\app.ico `
        --include-data-dir=data=data --include-data-dir=assets=assets `
        --include-data-dir=plugins_examples=plugins_examples `
        --include-package-data=GeoToolCN --include-package=pillow_heif --include-package=rawpy --include-package=onnxruntime `
        --output-dir=build\nuitka --output-filename=PhotoCrate.exe launcher.py
    if ($LASTEXITCODE -ne 0) { Write-Host "编译失败" -ForegroundColor Red; Pause-Exit 1 }
    $src = Join-Path $root "build\nuitka\launcher.dist"
    $dist = Join-Path $root "dist\PhotoCrate"
    if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $dist) | Out-Null
    Copy-Item $src $dist -Recurse
}

Write-Host "[3/3] 放入 ExifTool 与插件目录…" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $dist "tools") | Out-Null
Copy-Item "tools\exiftool" (Join-Path $dist "tools\exiftool") -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $dist "plugins") | Out-Null
Copy-Item "plugins\README.txt" (Join-Path $dist "plugins\README.txt") -Force

Write-Host ""
Write-Host "完成：$dist\PhotoCrate.exe" -ForegroundColor Green

# 可选：Inno Setup 安装包
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "检测到 Inno Setup，生成安装包…" -ForegroundColor Cyan
    & $iscc "installer.iss"
    if ($LASTEXITCODE -eq 0) { Write-Host "安装包：$root\dist\PhotoCrate-Setup.exe" -ForegroundColor Green }
} else {
    Write-Host "（如需安装包：安装 Inno Setup 6 后重新运行本脚本，或直接把 dist\PhotoCrate 文件夹压缩分享）" -ForegroundColor DarkGray
}
Pause-Exit 0
