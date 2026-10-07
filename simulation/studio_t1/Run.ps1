param([string]$StudioContainer = 'rytls-t1')
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$logPath = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$studio = & docker inspect $StudioContainer 2>$null | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $studio[0].State.Running) {
    throw 'Open T1 Test in Booster Studio before starting the demo.'
}
if (-not ($studio[0].Config.Env -contains 'SCENE_KEY=default_pitch_T1')) {
    throw 'This profile requires the Studio T1 virtual robot.'
}
& docker image inspect robocup-t1-demo:prepared *> $null
if ($LASTEXITCODE -ne 0) { throw 'The prepared local Kilted demo image is missing. See README.md.' }
$existing = & docker inspect robocup-t1-live 2>$null
if ($LASTEXITCODE -eq 0) {
    $existingInfo = $existing | ConvertFrom-Json
    if ($existingInfo[0].Config.Labels.task -ne 'robocup-studio-demo') {
        throw 'The container name robocup-t1-live belongs to another task.'
    }
    & docker stop robocup-t1-live | Out-Null
    & docker rm robocup-t1-live | Out-Null
}
& docker run -d --name robocup-t1-live --label task=robocup-studio-demo `
    --network "container:$StudioContainer" `
    --mount "type=bind,source=$repoPath,target=/source,readonly" `
    --mount "type=bind,source=$logPath,target=/work/studio-logs" `
    --entrypoint /bin/bash robocup-t1-demo:prepared /source/simulation/studio_t1/start.sh
if ($LASTEXITCODE -ne 0) { throw 'Demo container could not start.' }
Write-Host "Demo started. Logs: $logPath"
