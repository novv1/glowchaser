<#
.SYNOPSIS
    逐光者 GlowChaser —— 一键启动 / 自动清理脚本（Windows）

.DESCRIPTION
    1. 启动 FastAPI 服务（uvicorn），自动避开端口冲突、自动复用已运行的实例
    2. 自动打开浏览器（Edge/Chrome 独立 App 窗口，便于检测关闭动作）
    3. 关闭浏览器窗口后，自动停止服务、释放端口、清理残留进程
    4. 三重保险保证不残留后台进程：
         a) 正常退出：脚本 finally 块显式杀进程树
         b) 直接关闭命令行窗口：Windows Job Object（KILL_ON_JOB_CLOSE）
         c) Job Object 不可用时：独立看门狗进程监视宿主，宿主消失即清理

.PARAMETER Port
    指定服务端口，默认 8000。也可用环境变量 GLOWCHASER_PORT 覆盖。

.PARAMETER HostIp
    绑定地址，默认 127.0.0.1（仅本机）。用 -HostIp 0.0.0.0 可让局域网/手机访问。

.PARAMETER NoBrowser
    只启动服务，不打开浏览器；按 Q 键停止服务。

.PARAMETER CreateShortcut
    在桌面创建快捷方式后退出。

.PARAMETER Watchdog
    内部使用（看门狗模式），不要手动调用。
#>
param(
    [int]$Port = 0,
    [string]$HostIp = '127.0.0.1',
    [switch]$NoBrowser,
    [switch]$CreateShortcut,
    # --- 以下为看门狗内部参数 ---
    [switch]$Watchdog,
    [int]$ParentPid = 0,
    [string]$ParentStartTime = '',
    [int]$TargetPid = 0
)

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

# ============================================================
#  基础工具
# ============================================================

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot 'app\main.py'))) {
    $ProjectRoot = 'E:\Glowchaser'
}
if (-not (Test-Path (Join-Path $ProjectRoot 'app\main.py'))) {
    Write-Host "找不到项目根目录（app\main.py）。请把脚本放在 <项目根>\scripts\ 下。" -ForegroundColor Red
    exit 1
}
$StateFile = Join-Path $env:TEMP 'glowchaser-service.json'

function Init-Console {
    try { chcp 65001 | Out-Null } catch {}
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    try { $Host.UI.RawUI.WindowTitle = 'GlowChaser 逐光者' } catch {}
}

function Write-Step { param($m) Write-Host "  $m" -ForegroundColor DarkGray }
function Write-Ok   { param($m) Write-Host "  [OK]  $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  [!!]  $m" -ForegroundColor Yellow }
function Write-Err  { param($m) Write-Host "  [XX]  $m" -ForegroundColor Red }

function Stop-ProcTree {
    param([int]$ProcId)
    if (-not $ProcId -or $ProcId -le 0) { return }
    if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) { return }
    try { & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcId /T /F 2>&1 | Out-Null } catch {}
    Start-Sleep -Milliseconds 400
    if (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {
        try { Stop-Process -Id $ProcId -Force -ErrorAction Stop } catch {}
    }
}

function Test-PortOpen {
    param([int]$P)
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $P); return $c.Connected } catch { return $false } finally { $c.Dispose() }
}

function Get-PortOwner {
    param([int]$P)
    $c = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess } else { return 0 }
}

function Test-GlowchaserApi {
    param([int]$P)
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$P/api/cities" -TimeoutSec 3
        return ($null -ne $r.cities)
    } catch { return $false }
}

function Get-NextFreePort {
    param([int]$Start, [int]$Count = 20)
    for ($i = 0; $i -lt $Count; $i++) {
        $p = $Start + $i
        if (-not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) { return $p }
    }
    return 0
}

function Find-Python {
    $cands = @()
    if ($env:GLOWCHASER_PYTHON) { $cands += $env:GLOWCHASER_PYTHON }
    $cands += (Join-Path $env:USERPROFILE '.workbuddy\binaries\python\envs\default\Scripts\python.exe')
    $cands += (Join-Path $ProjectRoot '.venv\Scripts\python.exe')
    $cands += (Join-Path $ProjectRoot 'venv\Scripts\python.exe')
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $cands += $cmd.Source }
    foreach ($c in $cands) {
        if (-not $c) { continue }
        if (-not (Test-Path $c)) { continue }
        try {
            $out = & $c -c "import uvicorn, fastapi; print('glowchaser-ok')" 2>$null
            if ($out -match 'glowchaser-ok') { return (Resolve-Path $c).Path }
        } catch {}
    }
    return $null
}

function Get-BrowserExe {
    $cands = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

function Get-ProcessByCommandLine {
    param([string]$ExeName, [string]$Marker)
    Get-CimInstance Win32_Process -Filter "Name='$ExeName'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Marker*" }
}

# 检测用户是否按下 Q / Esc 请求停止；输入被重定向或无控制台时安全返回 $false
function Test-StopKeyPressed {
    try {
        if (-not [Console]::KeyAvailable) { return $false }
        $k = [Console]::ReadKey($true)
        return ($k.Key -eq 'Q' -or $k.Key -eq 'Escape')
    } catch { return $false }
}

# --- Windows Job Object：宿主进程被强杀时，内核自动终结组内所有进程 ---
function New-KillOnCloseJob {
    try {
        if (-not ('GcJobObject' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class GcJobObject {
    [StructLayout(LayoutKind.Sequential)]
    public struct BASIC_LIMIT {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public IntPtr MinimumWorkingSetSize;
        public IntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public IntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attr, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, ref BASIC_LIMIT info, uint len);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);
    public static IntPtr Create() {
        IntPtr h = CreateJobObject(IntPtr.Zero, null);
        if (h == IntPtr.Zero) return IntPtr.Zero;
        BASIC_LIMIT b = new BASIC_LIMIT();
        b.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(h, 2, ref b, (uint)Marshal.SizeOf(typeof(BASIC_LIMIT)))) return IntPtr.Zero;
        return h;
    }
    public static bool Assign(IntPtr hJob, int pid) {
        using (System.Diagnostics.Process p = System.Diagnostics.Process.GetProcessById(pid)) {
            return AssignProcessToJobObject(hJob, p.Handle);
        }
    }
}
'@ -ErrorAction Stop
        }
        return [GcJobObject]::Create()
    } catch { return [IntPtr]::Zero }
}

function New-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $bat = Join-Path $PSScriptRoot 'GlowChaser-Launcher.bat'
    if (-not (Test-Path $bat)) {
        Write-Err "未找到启动器：$bat"
        return
    }
    $lnkPath = Join-Path $desktop '逐光者 GlowChaser.lnk'
    $py = Find-Python
    $icon = if ($py) { "$py,0" } else { "$env:SystemRoot\System32\shell32.dll,13" }
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = $bat
    $sc.WorkingDirectory = $ProjectRoot
    $sc.IconLocation = $icon
    $sc.Description = '启动逐光者服务并打开浏览器，关窗即停'
    $sc.Save()
    Write-Ok "桌面快捷方式已创建：$lnkPath"
}

# ============================================================
#  模式一：看门狗（独立隐藏进程）
# ============================================================
if ($Watchdog) {
    try {
        while ($true) {
            Start-Sleep -Seconds 2
            $pp = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
            if (-not $pp) { break }
            if ($ParentStartTime -and $pp.StartTime.ToString('o') -ne $ParentStartTime) { break }
        }
    } catch {}
    finally {
        Stop-ProcTree $TargetPid
        if (Test-Path $StateFile) { Remove-Item $StateFile -Force -ErrorAction SilentlyContinue }
    }
    exit 0
}

# ============================================================
#  模式二：创建桌面快捷方式
# ============================================================
Init-Console
if ($CreateShortcut) {
    Write-Host ''
    Write-Host '  GlowChaser 逐光者 · 创建桌面快捷方式' -ForegroundColor Cyan
    Write-Host ''
    New-DesktopShortcut
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 0
}

# ============================================================
#  模式三：主流程
# ============================================================

if ($Port -le 0) {
    $Port = if ($env:GLOWCHASER_PORT) { [int]$env:GLOWCHASER_PORT } else { 8000 }
}

Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════╗' -ForegroundColor DarkCyan
Write-Host '  ║   逐 光 者  GlowChaser  ·  本地服务启动器    ║' -ForegroundColor Cyan
Write-Host '  ╚══════════════════════════════════════════════╝' -ForegroundColor DarkCyan
Write-Host ''
Write-Step "项目目录：$ProjectRoot"
Write-Host ''

# --- 1. 定位 Python ---
$python = Find-Python
if (-not $python) {
    Write-Err '未找到可用的 Python（需已安装 uvicorn + fastapi）。'
    Write-Step '请安装依赖：pip install -r app\requirements.txt'
    Write-Step '或设置环境变量 GLOWCHASER_PYTHON 指向 python.exe'
    Write-Host ''
    Read-Host '  按回车键退出'
    exit 1
}
Write-Ok "Python：$python"
Write-Host ''

# --- 2. 清理上次异常退出的残留实例 ---
if (Test-Path $StateFile) {
    try {
        $st = Get-Content $StateFile -Raw | ConvertFrom-Json
        if ($st -and $st.pid) {
            $old = Get-Process -Id $st.pid -ErrorAction SilentlyContinue
            if ($old -and $old.ProcessName -match 'python') {
                Write-Warn "发现上次未正常退出的服务实例 (PID $($st.pid))，正在清理…"
                Stop-ProcTree ([int]$st.pid)
            }
        }
    } catch {}
    Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
}

# --- 3. 端口决策：复用 / 顺延 / 新建 ---
$reuse = $false
$serverPid = 0
$owner = Get-PortOwner $Port
if ($owner -gt 0) {
    if (Test-GlowchaserApi $Port) {
        $reuse = $true
        $serverPid = $owner
        Write-Warn "端口 $Port 上已有 GlowChaser 服务在运行 (PID $owner)，本次直接复用。"
        Write-Step '关闭浏览器后，该服务将由本脚本一并停止以释放端口。'
    } else {
        $op = Get-Process -Id $owner -ErrorAction SilentlyContinue
        $oname = if ($op) { $op.ProcessName } else { '系统/未知进程' }
        $newPort = Get-NextFreePort ($Port + 1)
        if ($newPort -gt 0) {
            Write-Warn "端口 $Port 已被占用（$oname, PID $owner），自动改用端口 $newPort"
            $Port = $newPort
        } else {
            Write-Err "端口 $Port 被占用且找不到可用端口，请手动释放后重试。"
            Write-Host ''
            Read-Host '  按回车键退出'
            exit 1
        }
    }
}

# --- 4. 启动服务 ---
if (-not $reuse) {
    $logDir = Join-Path $ProjectRoot 'data\logs'
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

    Write-Step "正在启动服务（端口 $Port）…"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    $psi.Arguments = "-m uvicorn app.main:app --host $HostIp --port $Port"
    $psi.WorkingDirectory = $ProjectRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false
    $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $psi.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'

    try {
        $server = [System.Diagnostics.Process]::Start($psi)
    } catch {
        Write-Err "服务启动失败：$($_.Exception.Message)"
        Write-Host ''
        Read-Host '  按回车键退出'
        exit 1
    }
    $serverPid = $server.Id

    # 立即纳入 Job Object：本进程无论以何种方式终止，服务进程都会被内核终结
    $jobHandle = New-KillOnCloseJob
    $jobOk = $false
    if ($jobHandle -ne [IntPtr]::Zero) {
        $jobOk = [GcJobObject]::Assign($jobHandle, $serverPid)
        $jobNote = if ($jobOk) { '已启用' } else { '不可用（宿主进程已在另一个 Job 中），由看门狗接管' }
    } else {
        $jobNote = '不可用（创建失败），由看门狗接管'
    }

    # Job Object 不可用时的兜底：独立看门狗
    $wdPid = 0
    if (-not $jobOk) {
        $myStart = (Get-Process -Id $PID).StartTime.ToString('o')
        $wdArgs = "-NoProfile -NoLogo -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Watchdog -ParentPid $PID -ParentStartTime `"$myStart`" -TargetPid $serverPid"
        try {
            $wd = Start-Process powershell.exe -ArgumentList $wdArgs -WindowStyle Hidden -PassThru
            $wdPid = $wd.Id
        } catch { $wdPid = 0 }
    }

    # 健康检查
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if ($server.HasExited) { break }
        if (Test-GlowchaserApi $Port) { $ready = $true; break }
    }
    if (-not $ready) {
        Write-Err "服务启动超时或异常退出（端口 $Port）。请查看上方 uvicorn 输出。"
        Stop-ProcTree $serverPid
        if ($wdPid) { Stop-ProcTree $wdPid }
        Write-Host ''
        Read-Host '  按回车键退出'
        exit 1
    }

    @{ pid = $serverPid; port = $Port; started = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

    Write-Ok "服务已就绪（PID $serverPid）"
    Write-Step "清理保障：Job Object - $jobNote"
    Write-Step "清理保障：看门狗进程 - $(if ($wdPid) { "PID $wdPid" } else { '未启用' })"
} else {
    $wdPid = 0
}

$url = if ($HostIp -eq '0.0.0.0') { "http://localhost:$Port" } else { "http://$HostIp`:$Port" }
Write-Host ''
Write-Host "  ──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  访问地址：$url" -ForegroundColor White
Write-Host "  关闭浏览器窗口后，服务会自动停止并释放端口 $Port" -ForegroundColor DarkGray
Write-Host "  在本窗口按 Q 可随时手动停止服务" -ForegroundColor DarkGray
Write-Host "  ──────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ''

# --- 5. 打开浏览器并等待其关闭 ---
$browserProc = $null
$profileDir = Join-Path $env:LOCALAPPDATA 'GlowChaser\BrowserProfile'
$exeName = $null

if (-not $NoBrowser) {
    $browser = Get-BrowserExe
    if ($browser) {
        $exeName = Split-Path $browser -Leaf
        if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }
        $bArgs = @("--app=$url", "--user-data-dir=`"$profileDir`"", '--no-first-run',
                   '--no-default-browser-check', '--disable-features=Translate', '--window-size=1440,900')
        Write-Step "正在打开浏览器（独立窗口，便于检测关闭）…"
        try { $browserProc = Start-Process -FilePath $browser -ArgumentList $bArgs -PassThru }
        catch { Write-Warn "浏览器启动失败：$($_.Exception.Message)" }
    } else {
        Write-Warn '未检测到 Edge / Chrome，将使用系统默认浏览器打开。'
        Write-Step '（默认浏览器无法自动检测关闭，请在本窗口按 Q 停止服务）'
        try { Start-Process $url | Out-Null } catch {}
    }
}

try {
    if ($browserProc) {
        while ($true) {
            Start-Sleep -Milliseconds 600
            # 用户按 Q 手动停止
            if (Test-StopKeyPressed) { break }
            if ($browserProc.HasExited) {
                # 再确认该独立 profile 下没有残留进程（Edge/Chrome 后台进程）
                Start-Sleep -Seconds 2
                $rest = @(Get-ProcessByCommandLine -ExeName $exeName -Marker $profileDir)
                if ($rest.Count -eq 0) { break }
                Write-Step '浏览器主窗口已关闭，正在清理浏览器残留进程…'
                foreach ($r in $rest) { Stop-ProcTree ([int]$r.ProcessId) }
                break
            }
        }
    } else {
        Write-Host '  服务运行中。按 Q 停止服务…' -ForegroundColor Yellow
        while (-not (Test-StopKeyPressed)) { Start-Sleep -Milliseconds 500 }
    }
}
finally {
    # --- 6. 清理 ---
    Write-Host ''
    Write-Step '正在停止服务并释放端口…'
    Stop-ProcTree $serverPid
    if ($wdPid) { Stop-ProcTree $wdPid }

    # 端口兜底释放（针对复用端口等边界情况）
    for ($i = 0; $i -lt 10; $i++) {
        $o = Get-PortOwner $Port
        if ($o -eq 0) { break }
        Stop-ProcTree $o
        Start-Sleep -Milliseconds 400
    }

    if (Test-Path $StateFile) { Remove-Item $StateFile -Force -ErrorAction SilentlyContinue }
    if ((Get-PortOwner $Port) -eq 0) { Write-Ok "端口 $Port 已释放，服务已停止" }
    else { Write-Warn "端口 $Port 仍被占用，可手动执行：netstat -ano | findstr :$Port" }
    Write-Host ''
    Start-Sleep -Milliseconds 700
}
