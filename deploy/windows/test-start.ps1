# CI check for start.cmd / reelvault.ps1 on a real Windows host (run from the repo root
# after "uv sync" in backend and "npm run build" in frontend).
$ErrorActionPreference = "Stop"
$start = Join-Path $PSScriptRoot "start.cmd"
$ps1 = Join-Path $PSScriptRoot "reelvault.ps1"
$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$headers = @{ "X-Requested-With" = "XMLHttpRequest" }

function Wait-Health([int]$Port, [string]$DataDir) {
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/healthz" -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Get-ChildItem $DataDir -Filter "reelvault.log*" -ErrorAction SilentlyContinue |
        ForEach-Object { "--- $($_.Name)"; Get-Content $_.FullName -Tail 40 }
    throw "ReelVault did not answer on port $Port"
}

function Test-Listening([int]$Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# 1. Missing tools fail with a clear message instead of a hidden hang.
$bad = Join-Path $temp "rv-missing-uv"
New-Item -ItemType Directory -Force $bad | Out-Null
Set-Content -Path (Join-Path $bad "reelvault.env") -Value "REELVAULT_UV=C:\missing\uv.exe"
$output = & $start -DataDir $bad 2>&1 | Out-String
if ($LASTEXITCODE -ne 1 -or $output -notmatch "REELVAULT_UV") {
    throw "missing uv should exit 1 with a hint, got $LASTEXITCODE`n$output"
}
Write-Host "ok: missing uv reported"

# 2. Foreground run with a data directory containing spaces, then a real upload and
#    ingest through ffmpeg.
$data = Join-Path $temp "ReelVault data"
$port = 34124
$server = Start-Process -FilePath "powershell.exe" -PassThru -NoNewWindow -ArgumentList (
    "-NoProfile -ExecutionPolicy Bypass -File `"$ps1`" -DataDir `"$data`" -Port $port")
try {
    Wait-Health $port $data
    $base = "http://127.0.0.1:$port"
    $index = Invoke-WebRequest -UseBasicParsing "$base/"
    if ($index.Content -notmatch 'id="root"') { throw "frontend not served" }
    Invoke-RestMethod -Method Post "$base/api/auth/setup" -Headers $headers -SessionVariable web `
        -ContentType "application/json" -Body '{"username":"admin","password":"secret123"}' | Out-Null
    $clip = Join-Path $temp "rv-clip.mp4"
    ffmpeg -v error -y -f lavfi -i testsrc2=size=320x240:rate=25 -f lavfi -i sine -t 3 `
        -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest $clip
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg fixture failed" }
    $bytes = [System.IO.File]::ReadAllBytes($clip)
    $upload = Invoke-RestMethod -Method Post "$base/api/uploads" -Headers $headers -WebSession $web `
        -ContentType "application/json" -Body (@{ filename = "clip.mp4"; size = $bytes.Length } | ConvertTo-Json)
    Invoke-RestMethod -Method Put "$base/api/uploads/$($upload.id)?offset=0" -Headers $headers `
        -WebSession $web -ContentType "application/octet-stream" -Body $bytes | Out-Null
    $done = Invoke-RestMethod -Method Post "$base/api/uploads/$($upload.id)/complete" -Headers $headers `
        -WebSession $web -ContentType "application/json" -Body "{}"
    $videoId = if ($done.id) { $done.id } else { $done.video.id }
    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 1
        $video = Invoke-RestMethod "$base/api/videos/$videoId" -Headers $headers -WebSession $web
    } while ($video.status -eq "processing" -and (Get-Date) -lt $deadline)
    if ($video.status -ne "ready") { throw "video ingest ended as $($video.status): $($video.error)" }
    Write-Host "ok: foreground run, upload and ingest ($($video.width)x$($video.height))"
} finally {
    taskkill /PID $server.Id /T /F | Out-Null
}

# 3. Background scheduled task: starts hidden, writes the log, and uninstall stops the
#    server it started. Task processes don't see this job's PATH, so pin the tools.
$taskData = Join-Path $temp "ReelVault task"
$taskPort = 34125
New-Item -ItemType Directory -Force $taskData | Out-Null
Set-Content -Path (Join-Path $taskData "reelvault.env") -Value @(
    "REELVAULT_UV=$((Get-Command uv).Source)",
    "REELVAULT_FFMPEG=$((Get-Command ffmpeg).Source)",
    "REELVAULT_FFPROBE=$((Get-Command ffprobe).Source)"
)
& $start -Install -DataDir $taskData -Port $taskPort
if ($LASTEXITCODE -ne 0) { throw "install failed with $LASTEXITCODE" }
try {
    Wait-Health $taskPort $taskData
    $log = Get-Content (Join-Path $taskData "reelvault.log") -Raw
    if ($log -notmatch "Uvicorn running") { throw "server log missing startup line:`n$log" }
    Write-Host "ok: scheduled task serves and logs"
} catch {
    Get-ScheduledTaskInfo -TaskName ReelVault -ErrorAction SilentlyContinue | Format-List | Out-String
    throw
} finally {
    & $start -Uninstall -Port $taskPort
}
Start-Sleep -Seconds 3
if (Test-Listening $taskPort) { throw "server still listening after uninstall" }
if (Get-ScheduledTask -TaskName ReelVault -ErrorAction SilentlyContinue) { throw "task not removed" }
Write-Host "ok: uninstall stopped the server and removed the task"
