<#
.SYNOPSIS
  在 Windows 上原生运行 ReelVault，或注册为登录后自动启动的后台任务。

.EXAMPLE
  .\deploy\windows\start.cmd                          # 前台运行，数据在 %USERPROFILE%\ReelVault
  .\deploy\windows\start.cmd -DataDir D:\ReelVault    # 指定数据目录
  .\deploy\windows\start.cmd -Install -DataDir D:\ReelVault   # 登录后自动在后台运行
  .\deploy\windows\start.cmd -Uninstall               # 删除后台任务

  其他配置（如 REELVAULT_WORKERS、REELVAULT_FFMPEG）写在数据目录的 reelvault.env 中，
  每行一个 NAME=value。后台任务不使用终端里临时添加的 PATH；uv 或 ffmpeg
  不在系统 PATH 中时，用 REELVAULT_UV、REELVAULT_FFMPEG、REELVAULT_FFPROBE 指定完整路径。
#>
param(
    [string]$DataDir = (Join-Path $env:USERPROFILE "ReelVault"),
    [int]$Port = 34123,
    [switch]$Install,
    [switch]$Uninstall,
    # Used by the scheduled task: write output to log files instead of the console.
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$TaskName = "ReelVault"
$RestartExitCode = 75  # reelvault.updates.RESTART_EXIT_CODE
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$Log = Join-Path $DataDir "reelvault.log"

function Fail([string]$Message) {
    # A hidden background task has no console; leave the reason in the log.
    if ($Background) { Add-Content -Encoding UTF8 -Path $Log -Value $Message }
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Stop-Task {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { return $false }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Stopping the task ends PowerShell; the server it started may outlive it.
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } |
        Where-Object { $_.ProcessName -eq "python" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    return $true
}

if ($Uninstall) {
    if (Stop-Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除后台任务 $TaskName"
    } else {
        Write-Host "没有找到后台任务 $TaskName"
    }
    exit 0
}

# ------------------------------------------------------------------ checks

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$EnvFile = Join-Path $DataDir "reelvault.env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content -Encoding UTF8 $EnvFile) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $name, $value = $line.Split("=", 2)
            Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim().Trim('"')
        }
    }
}

$Uv = if ($env:REELVAULT_UV) { $env:REELVAULT_UV } else { "uv" }
if (-not (Get-Command $Uv -ErrorAction SilentlyContinue)) {
    Fail ("找不到 uv。安装：winget install --id astral-sh.uv -e，然后重新打开终端；" +
          "或在 $EnvFile 中设置 REELVAULT_UV=完整路径")
}

foreach ($tool in @("ffmpeg", "ffprobe")) {
    $configured = [Environment]::GetEnvironmentVariable("REELVAULT_$($tool.ToUpper())")
    if (-not ($configured -or (Get-Command $tool -ErrorAction SilentlyContinue))) {
        Fail ("找不到 $tool。安装：winget install --id Gyan.FFmpeg -e，然后重新打开终端；" +
              "或在 $EnvFile 中设置 REELVAULT_$($tool.ToUpper())=完整路径")
    }
}

$static = @(
    (Join-Path $Backend "reelvault\static\index.html"),
    (Join-Path $Root "frontend\dist\index.html")
) | Where-Object { Test-Path $_ }
if (-not $static) {
    Fail "尚未构建前端。使用发布包，或在源码目录运行：cd frontend; npm ci; npm run build"
}

# ------------------------------------------------------------------ install

if ($Install) {
    $arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass " +
        "-File `"$PSCommandPath`" -Background -DataDir `"$DataDir`" -Port $Port"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments `
        -WorkingDirectory $Backend
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Stop-Task | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "ReelVault video library" -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "ReelVault 已在后台运行，登录后自动启动：http://localhost:$Port"
    Write-Host "数据目录：$DataDir   日志：$Log"
    exit 0
}

# ------------------------------------------------------------------ run

$env:REELVAULT_DATA_DIR = $DataDir
$env:REELVAULT_PORT = "$Port"
# UTF-8 for logs and files, as on Linux.
$env:PYTHONUTF8 = "1"
# One-click upgrades are only verified for the Ubuntu install; upgrade by
# downloading the new release instead. Set to true in reelvault.env to opt in.
if (-not $env:REELVAULT_ALLOW_SELF_UPDATE) { $env:REELVAULT_ALLOW_SELF_UPDATE = "false" }

$uvArgs = @("run", "--frozen", "--no-dev", "--project", $Backend, "reelvault")
Set-Location $Backend
while ($true) {
    if ($Background) {
        if (Test-Path $Log) { Move-Item -Force $Log "$Log.1" }
        # Windows PowerShell joins -ArgumentList unquoted; paths may contain spaces.
        $quoted = ($uvArgs | ForEach-Object { '"' + $_ + '"' }) -join " "
        $process = Start-Process -FilePath $Uv -ArgumentList $quoted -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput "$Log.out" -RedirectStandardError $Log
        $code = $process.ExitCode
    } else {
        Write-Host "ReelVault：http://localhost:$Port   数据目录：$DataDir   Ctrl+C 停止"
        & $Uv @uvArgs
        $code = $LASTEXITCODE
    }
    if ($code -eq $RestartExitCode) {
        Write-Host "升级完成，正在重新启动"
    } elseif ($Background -and $code -ne 0) {
        # Like systemd Restart=on-failure: retry after a crash, keep a clean stop.
        Start-Sleep -Seconds 5
    } else {
        exit $code
    }
}
