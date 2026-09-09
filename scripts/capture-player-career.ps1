# A bounded normal in-game lookup recording. Preview does not start a recorder.
# This is separate from the older handshake experiment: the game stays open.
[CmdletBinding()]
param(
    [switch]$Record,
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$ExpectedGameProcessId,
    [switch]$FromLauncher,
    [ValidatePattern('^[0-9a-f]{64}$')][string]$ReviewedScopeFingerprint,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$RunId = ([guid]::NewGuid().ToString('N'))
)
$ErrorActionPreference = 'Stop'
# Both peers were observed on game PID 21752 on 2026-09-08. No discovered
# replacement IPs, HTTPS peers, UDP or additional providers are added at runtime.
$remoteIps = @('34.60.237.47','35.236.255.68')
$expectedPorts = @(10203,12101)
$durationSeconds = if ($FromLauncher) { 180 } else { 90 }
$observedGameId = $null

function Get-PlayerCareerConnections {
    $games = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
    if ($FromLauncher -and $games.Count -eq 0 -and $null -eq $script:observedGameId) { return }
    if ($games.Count -ne 1 -or (-not $FromLauncher -and $games[0].Id -ne $ExpectedGameProcessId) -or
        ($null -ne $script:observedGameId -and $games[0].Id -ne $script:observedGameId)) {
        throw 'The expected game process is no longer the only running game. Nothing new will be captured.'
    }
    $script:observedGameId=[int]$games[0].Id
    @(Get-CimInstance -Namespace root/StandardCimv2 -ClassName MSFT_NetTCPConnection -Filter ('OwningProcess = {0} AND State = 5' -f $script:observedGameId) -ErrorAction Stop |
        Where-Object { $_.RemoteAddress -in $remoteIps } |
        Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort)
}
function Assert-PlayerCareerPeers {
    param($Connections)
    for ($index=0; $index -lt $remoteIps.Count; $index++) {
        if (@($Connections | Where-Object { $_.RemoteAddress -eq $remoteIps[$index] -and $_.RemotePort -eq $expectedPorts[$index] }).Count -ne 1) {
            throw 'The previously observed game peers have changed. Preview again; no fallback scope is allowed.'
        }
    }
}

function Assert-PlayerCareerStart {
    if ($FromLauncher) {
        if (@(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue).Count -ne 0) {
            throw 'Startup capture requires the game closed at its launcher. Nothing started.'
        }
    } else { Assert-PlayerCareerPeers @(Get-PlayerCareerConnections) }
}
Assert-PlayerCareerStart
$initial = @()
if (-not $FromLauncher) { $initial = @(Get-PlayerCareerConnections) }
$scopeText = 'player-career-v2|physical|IPv4|TCP|all-ports|65535|32MB|' + $durationSeconds + 's|' + $ExpectedGameProcessId + '|' + [bool]$FromLauncher + '|' + ($remoteIps -join ',')
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $fingerprint = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($scopeText))).Replace('-','').ToLowerInvariant()
} finally { $hasher.Dispose() }
$plan = [ordered]@{
    capture_kind='normal_player_career_lookup'; expected_game_process_id=$ExpectedGameProcessId
    remote_ipv4_addresses=$remoteIps; initial_connections=$initial
    duration_seconds=$durationSeconds; capture_type='physical'; ip_version=4; transport='TCP'
    tcp_ports_restricted=$false; per_packet_byte_cap=65535; file_size_limit_mb=32
    file_mode='single'; other_processes_using_same_ips_may_be_captured=$true
    requires_existing_game=(-not $FromLauncher); requires_game_closed_at_start=[bool]$FromLauncher
    peer_source=$(if ($FromLauncher) {'previously_observed_same_two_ips_2026_09_08'} else {'current_game_connections'})
    changes_game_or_login=$false; persistent=$false
    scope_fingerprint=$fingerprint; capture_scope_verified=$false
}
$plan | ConvertTo-Json -Depth 5 | Write-Output
if (-not $Record) { return }
if (-not $ReviewedScopeFingerprint -or $ReviewedScopeFingerprint -cne $fingerprint) {
    throw 'Recording requires the unchanged reviewed preview fingerprint. Nothing started.'
}
$capturePrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'An administrator process is required. This script does not elevate itself.'
}
$sessionName = 'MAKPlayer' + $RunId.Substring(0,16)
$sessions = (& logman.exe query -ets 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $sessions -match '(?im)^\s*(?:PktMon\b|NetTrace\b|MAKCareer\w*\b|MAKPlayer\w*\b)') {
    throw 'Could not confirm an idle recorder. Existing sessions were left untouched.'
}
Assert-PlayerCareerStart
$folder = Join-Path $env:LOCALAPPDATA ('MarvelAccountKeeper\diagnostics\player-career-' + $RunId)
$null = New-Item -ItemType Directory -Path $folder -ErrorAction Stop
$etlPath = Join-Path $folder 'career.etl'
$reportPath = Join-Path $folder 'capture-report.json'
$transcriptPath = Join-Path $folder 'capture-console.txt'
$observations = [Collections.Generic.List[object]]::new()
$startAttempted=$false; $started=$false; $stopped=$false
$startedAt=$null; $stoppedAt=$null; $startExit=$null; $stopExit=$null; $failure=$null
$null = Start-Transcript -LiteralPath $transcriptPath
try {
    $startArgs = @('trace','start',('sessionname=' + $sessionName),
        'capture=yes','capturetype=physical','report=disabled','persistent=no',
        ('tracefile=' + $etlPath),'maxSize=32','fileMode=single','overwrite=no',
        'correlation=disabled','perfMerge=no','CaptureMultiLayer=no',
        'Ethernet.Type=IPv4','Protocol=TCP',
        ('IPv4.Address=(' + ($remoteIps -join ',') + ')'),'PacketTruncateBytes=65535')
    Assert-PlayerCareerStart
    $startAttempted=$true
    & netsh.exe @startArgs | Out-Host
    $startExit=$LASTEXITCODE
    if ($startExit -ne 0) { throw 'The named capture did not start. No fallback was attempted.' }
    $started=$true
    $startedAt=[DateTime]::UtcNow.ToString('o')
    $timer=[Diagnostics.Stopwatch]::StartNew()
    if ($FromLauncher) { Write-Host 'PLAYER STARTUP CAPTURE READY: press Launch, then search KovaaksKid2008 and open Career Overview.' }
    else { Write-Host 'PLAYER CAREER CAPTURE READY: now search KovaaksKid2008 and open Career Overview.' }
    Write-Host ('Leave the overview visible. Do not change accounts or enter a match. Stops after ' + $durationSeconds + ' seconds.')
    while ($timer.Elapsed.TotalSeconds -lt $durationSeconds) {
        $remainingMs=[int][Math]::Max(1,[Math]::Ceiling(1000*$durationSeconds-$timer.Elapsed.TotalMilliseconds))
        Start-Sleep -Milliseconds ([Math]::Min(1000,$remainingMs))
        $connections=@(Get-PlayerCareerConnections)
        $observations.Add([pscustomobject]@{at_utc=[DateTime]::UtcNow.ToString('o');game_process_id=$script:observedGameId;connections=$connections})
    }
} catch {
    $failure=$_.Exception.Message
    Write-Warning $failure
} finally {
    if ($startAttempted) {
        try {
            & netsh.exe trace stop ('sessionname=' + $sessionName) | Out-Host
            $stopExit=$LASTEXITCODE
            $stopped=$stopExit -eq 0
            $stoppedAt=[DateTime]::UtcNow.ToString('o')
        } catch { $failure='Named-session stop failed: ' + $_.Exception.Message }
        if (-not $stopped) { Write-Warning ('Stop was not confirmed. Inspect only session ' + $sessionName + '. Do not issue a global stop.') }
    }
    [ordered]@{
        schema_version=1; plan=$plan; session_name=$sessionName
        started=$started; stopped=$stopped; start_attempted=$startAttempted
        start_exit_code=$startExit; stop_exit_code=$stopExit
        started_at_utc=$startedAt; stopped_at_utc=$stoppedAt
        observations=$observations.ToArray(); failure=$failure; etl_path=$etlPath
        every_packet_process_ownership_verified=$false; capture_scope_verified=$false
        career_access_verified=$false; network_requests_sent_by_helper=$false
    } | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    if ($stopped) { Write-Host 'PLAYER CAREER CAPTURE STOPPED. Keep the files local for offline inspection.' }
    Write-Host ('Local capture folder: ' + $folder)
    $null=Stop-Transcript
}
if (-not $started -or -not $stopped -or $failure) { throw 'Capture did not complete cleanly. See the local report; do not automatically retry.' }
