param([string]$StudioContainer = 'rytls-t1')
$ErrorActionPreference = 'Stop'
$library = Join-Path $env:APPDATA 'Booster Studio/simulator-library'
foreach ($artifact in @('fcisar_t1_robo_league_3v3.bscene', 'robot_t1_fcisar.basset')) {
    $source = Join-Path $PSScriptRoot "scenes/$artifact"
    if (-not (Test-Path -LiteralPath $source)) { throw "Missing generated artifact: $source. Run build_scene.py first." }
    $folder = if ($artifact.EndsWith('.bscene')) { 'scenes' } else { 'assets' }
    Copy-Item -LiteralPath $source -Destination (Join-Path $library "$folder/$artifact")
}
& docker cp (Join-Path $PSScriptRoot 'patch_studio_field.py') "${StudioContainer}:/tmp/fcisar-field-patch.py"
if ($LASTEXITCODE -ne 0) { throw 'Cannot copy the field configuration hook.' }
$patchOutput = & docker exec $StudioContainer python3 /tmp/fcisar-field-patch.py
if ($LASTEXITCODE -ne 0) { throw 'Studio referee source is incompatible with the custom-field configuration hook.' }
$patchOutput | Write-Host
if ($patchOutput -match '^Configured:') {
    # The parent process caches the extension schema before scene workers fork.
    & docker restart $StudioContainer | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restart Studio after installing the field schema.' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec $StudioContainer curl -sf --max-time 1 http://127.0.0.1:38383/health *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'Studio did not become ready after the field-schema restart.' }
}
& docker cp (Join-Path $PSScriptRoot 'studio_command.py') "${StudioContainer}:/tmp/fcisar-studio-command.py"
if ($LASTEXITCODE -ne 0) { throw 'Cannot copy the Studio scene command.' }
& docker exec $StudioContainer /usr/local/booster_robot/booster_robocup_sim/.venv/bin/python3 `
    /tmp/fcisar-studio-command.py switch_scene '{"scene_key":"fcisar_t1_robo_league_3v3"}'
if ($LASTEXITCODE -ne 0) { throw 'Studio did not accept the scene switch.' }
