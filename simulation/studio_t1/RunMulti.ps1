param([string]$StudioContainer = 'rytls-t1', [switch]$InstallScene)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$logPath = Join-Path $PSScriptRoot 'logs/multi'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$studio = & docker inspect $StudioContainer 2>$null | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $studio[0].State.Running) { throw 'Open the T1 virtual robot in Studio first.' }
& docker image inspect robocup-t1-demo:multi *> $null
if ($LASTEXITCODE -ne 0) { throw 'Build Dockerfile.multi first; see README.md.' }
if ($InstallScene) { & (Join-Path $PSScriptRoot 'InstallScene.ps1') -StudioContainer $StudioContainer }
$existing = & docker inspect robocup-t1-multi 2>$null
if ($LASTEXITCODE -eq 0) {
    $info = $existing | ConvertFrom-Json
    if ($info[0].Config.Labels.task -ne 'robocup-studio-multi') { throw 'Container name belongs to another task.' }
    & docker stop robocup-t1-multi | Out-Null
    & docker rm robocup-t1-multi | Out-Null
}
& docker run -d --name robocup-t1-multi --label task=robocup-studio-multi `
    --network "container:$StudioContainer" `
    --mount "type=bind,source=$repoPath,target=/source,readonly" `
    --mount "type=bind,source=$logPath,target=/work/studio-logs" `
    --entrypoint /bin/bash robocup-t1-demo:multi /source/simulation/studio_t1/start_multi.sh
if ($LASTEXITCODE -ne 0) { throw 'Multi-robot runtime failed to start.' }
Write-Host "Multi-robot container launched; startup is still in progress. Health report: $logPath/multi-health.json"
