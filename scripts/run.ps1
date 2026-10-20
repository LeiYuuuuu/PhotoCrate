# 影匣 PhotoCrate 启动脚本：首次运行自动创建虚拟环境、安装依赖、下载 ExifTool，然后启动程序。
# 运行过程同时写入程序目录下的 run.log，出问题时把它发给开发者即可。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$Host.UI.RawUI.WindowTitle = "影匣 PhotoCrate"
$log = Join-Path $root "run.log"
Set-Content -Path $log -Value ("==== " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " ====") -Encoding UTF8

function Say($text, $color = "Gray") {
    Write-Host $text -ForegroundColor $color
    Add-Content -Path $log -Value $text -Encoding UTF8
}

function Pause-Exit($code) {
    Add-Content -Path $log -Value "EXIT $code" -Encoding UTF8
    Write-Host ""
    Read-Host "按回车键退出"
    exit $code
}

function Run-Logged($exe, [string[]]$argList) {
    # 原生命令的输出同时显示并写入日志
    & $exe @argList 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        Add-Content -Path $log -Value $line -Encoding UTF8
    }
    return $LASTEXITCODE
}

Say "================================================" DarkGray
Say "  影匣 PHOTOCRATE // 本地照片分区整理终端" Yellow
Say "================================================" DarkGray

# ---- 1. 查找 Python 3.10+ ---------------------------------------------------
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$venvPyw = Join-Path $root ".venv\Scripts\pythonw.exe"

function Test-Py($exe, [string[]]$pre) {
    try {
        $out = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2]); print(1 if (3,10) <= sys.version_info[:2] <= (3,13) else 0)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and "$($out[-1])".Trim() -eq "1") { return "$($out[0])".Trim() }
    } catch { }
    return $null
}

if (-not (Test-Path $venvPy)) {
    $found = $null
    # 1) py 启动器 / PATH 中的 python
    foreach ($cand in @(@("py", "-3.12"), @("py", "-3.11"), @("py", "-3.13"), @("py", "-3"), @("python"))) {
        $exe = $cand[0]
        $pre = @($cand | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $v = Test-Py $exe $pre
        if ($v) { $found = @{ exe = $exe; pre = $pre; ver = $v }; break }
    }
    # 2) 常见安装位置（官方安装包、Anaconda、Miniconda）
    if (-not $found) {
        $paths = @()
        $paths += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
        $paths += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
        $paths += @("$env:USERPROFILE\miniconda3\python.exe", "$env:USERPROFILE\anaconda3\python.exe",
                    "$env:ProgramData\miniconda3\python.exe", "$env:ProgramData\anaconda3\python.exe")
        foreach ($p in $paths) {
            if (-not (Test-Path $p)) { continue }
            $v = Test-Py $p @()
            if ($v) { $found = @{ exe = $p; pre = @(); ver = $v }; break }
        }
    }
    if (-not $found) {
        Say ""
        Say "没有找到 Python 3.10 ~ 3.13。" Red
        Say "请安装 Python 3.12（安装时勾选 Add python.exe to PATH），然后重新双击 run.bat。"
        Start-Process "https://www.python.org/downloads/windows/"
        Pause-Exit 1
    }
    Say "使用 Python $($found.ver)：$($found.exe) $($found.pre -join ' ')" DarkGray
    Say "[1/3] 创建虚拟环境（只在第一次运行时需要）…" Cyan
    $code = Run-Logged $found.exe ($found.pre + @("-m", "venv", ".venv"))
    if ($code -ne 0 -or -not (Test-Path $venvPy)) { Say "创建虚拟环境失败" Red; Pause-Exit 1 }
}

# ---- 2. 安装 / 更新依赖（requirements.txt 变化时才重新安装） -------------------
$req = Join-Path $root "requirements.txt"
$stamp = Join-Path $root ".venv\.req_hash"
$hash = (Get-FileHash $req -Algorithm SHA256).Hash
$old = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if ($hash -ne $old) {
    Say "[2/3] 安装依赖库（首次约需几分钟）…" Cyan
    Run-Logged $venvPy @("-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check", "-q") | Out-Null
    $code = Run-Logged $venvPy @("-m", "pip", "install", "-r", $req, "--disable-pip-version-check", "--progress-bar", "off")
    if ($code -ne 0) {
        Say "默认源安装失败，改用清华镜像重试…" Yellow
        $code = Run-Logged $venvPy @("-m", "pip", "install", "-r", $req, "--disable-pip-version-check", "--progress-bar", "off",
                                     "-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
        if ($code -ne 0) { Say "依赖安装失败，请检查网络后重试。" Red; Pause-Exit 1 }
    }
    Set-Content -Path $stamp -Value $hash
}

# ---- 3. ExifTool ------------------------------------------------------------
$et = Join-Path $root "tools\exiftool\exiftool.exe"
if (-not (Test-Path $et)) {
    Say "[3/3] 下载 ExifTool（读取各品牌 RAW 元数据用）…" Cyan
    # 在本进程内运行下载脚本（另开 powershell 子进程时中文输出会因控制台代码页变成乱码）
    try {
        & (Join-Path $root "tools\get_exiftool.ps1") *>&1 | ForEach-Object { Say "$_" }
    } catch {
        Say ("ExifTool 下载出错：" + $_.Exception.Message) Yellow
    }
    if (-not (Test-Path $et)) {
        Say "ExifTool 下载失败，程序仍可运行（部分 RAW 日期可能读不到），之后可在「设置」中指定。" Yellow
    }
}

# ---- 4. 启动前自检，避免无窗口程序静默失败 ----------------------------------------
$code = Run-Logged $venvPy @("-c", "import photocrate.app, PySide6, rawpy, pillow_heif, cv2, GeoToolCN; print('self-check ok')")
if ($code -ne 0) { Say "程序自检失败，请把 run.log 发给开发者。" Red; Pause-Exit 1 }

Say ""
Say "正在启动…" Green
Start-Process -FilePath $venvPyw -ArgumentList "-m", "photocrate" -WorkingDirectory $root
Add-Content -Path $log -Value "LAUNCHED" -Encoding UTF8
