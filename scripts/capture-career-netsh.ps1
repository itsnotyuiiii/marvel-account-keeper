# One explicitly reviewed experiment. Preview is nonmutating; Record requires
# the preview fingerprint and separate user approval. Never expand these peers.
[CmdletBinding()]
param(
    [switch]$Record,
    [ValidatePattern('^[0-9a-f]{64}$')][string]$ReviewedScopeFingerprint,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$RunId = ([guid]::NewGuid().ToString('N'))
)
$ErrorActionPreference = 'Stop'
$remoteIps = @('35.236.255.68','8.221.212.39')
$durationSeconds = 90
$scopeText = 'netsh-v1|physical|IPv4|TCP|all-ports|65535|32MB|90s|' + ($remoteIps -join ',')
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $fingerprint = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($scopeText))).Replace('-','').ToLowerInvariant()
} finally { $hasher.Dispose() }
$plan = [ordered]@{
    capture_kind = 'approved_fixed_ipv4_tcp_netsh'
    remote_ipv4_addresses = $remoteIps
    peer_source = 'user_reviewed_game_connection_metadata_2026_09_06'
    duration_seconds = $durationSeconds
    capture_type = 'physical'
    ip_version = 4
    transport = 'TCP'
    tcp_ports_restricted = $false
    per_packet_byte_cap = 65535
    file_size_limit_mb = 32
    file_mode = 'single'
    other_processes_using_same_ips_may_be_captured = $true
    requires_game_closed_at_start = $true
    adds_new_remote_peers = $false
    changes_game_or_login = $false
    persistent = $false
    scope_fingerprint = $fingerprint
    capture_scope_verified = $false
}
$plan | ConvertTo-Json -Depth 4 | Write-Output
if (-not $Record) { return }
if (-not $ReviewedScopeFingerprint -or $ReviewedScopeFingerprint -cne $fingerprint) {
    throw 'Recording requires the unchanged preview fingerprint and explicit user approval. Nothing started.'
}
if (@(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue).Count -ne 0) {
    throw 'Close the game and remain at the launcher before recording. Nothing started.'
}
$captureIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$capturePrincipal = [Security.Principal.WindowsPrincipal]::new($captureIdentity)
if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'An administrator process is required. This script does not elevate itself.'
}
$sessionName = 'MAKCareer' + $RunId.Substring(0,16)
$sessions = (& logman.exe query -ets 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $sessions -match ('(?im)^\s*(?:PktMon\b|NetTrace\b|' + [regex]::Escape($sessionName) + '\b)')) {
    throw 'Could not confirm an idle recorder and unused session name. Existing sessions were left untouched.'
}
$folder = Join-Path $env:LOCALAPPDATA ('MarvelAccountKeeper\diagnostics\career-netsh-' + $RunId)
$null = New-Item -ItemType Directory -Path $folder -ErrorAction Stop
$etlPath = Join-Path $folder 'career.etl'
$reportPath = Join-Path $folder 'capture-report.json'
$transcriptPath = Join-Path $folder 'capture-console.txt'
$observations = [Collections.Generic.List[object]]::new()
$observedIds = @()
$startAttempted = $false
$started = $false
$stopped = $false
$startedAt = $null
$stoppedAt = $null
$startExit = $null
$stopExit = $null
$metadataFailed = $false
$failure = $null
$null = Start-Transcript -LiteralPath $transcriptPath
try {
    # IPv4 alone does not exclude other Ethernet types: require both filters.
    # No scenario, extra provider, broad 443 filter, installation or persistence.
    $startArgs = @('trace','start',('sessionname=' + $sessionName),
        'capture=yes','capturetype=physical','report=disabled','persistent=no',
        ('tracefile=' + $etlPath),'maxSize=32','fileMode=single','overwrite=no',
        'correlation=disabled','perfMerge=no','CaptureMultiLayer=no',
        'Ethernet.Type=IPv4','Protocol=TCP',
        ('IPv4.Address=(' + ($remoteIps -join ',') + ')'),'PacketTruncateBytes=65535')
    # Recheck immediately before starting; a preview is not permission to launch.
    if (@(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'The game started before the recorder. Nothing started.'
    }
    $startAttempted = $true
    & netsh.exe @startArgs | Out-Host
    $startExit = $LASTEXITCODE
    if ($startExit -ne 0) { throw 'The named Netsh capture did not start. No fallback capture was attempted.' }
    $started = $true
    $startedAt = [DateTime]::UtcNow.ToString('o')
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host 'NETSH CAPTURE READY: press Launch now, use the same account, and open your own Career.'
    Write-Host 'Stops after 90 seconds. Do not change accounts or start a match. New peers are not added.'
    while ($timer.Elapsed.TotalSeconds -lt $durationSeconds) {
        $remainingMs = [int][Math]::Max(1,[Math]::Ceiling(1000*$durationSeconds-$timer.Elapsed.TotalMilliseconds))
        Start-Sleep -Milliseconds ([Math]::Min(1000,$remainingMs))
        try {
            $games = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
            if ($games.Count -gt 1) { throw 'More than one game process appeared.' }
            $connections = @()
            $gameId = $null
            if ($games.Count -eq 1) {
                $gameId = [int]$games[0].Id
                if ($gameId -notin $observedIds) { $observedIds += $gameId }
                # Metadata only, not packet ownership or authority to add filters.
                $connections = @(Get-CimInstance -Namespace root/StandardCimv2 -ClassName MSFT_NetTCPConnection -Filter ('OwningProcess = {0} AND State = 5' -f $gameId) -ErrorAction Stop |
                    Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort)
            }
            $observations.Add([pscustomobject]@{at_utc=[DateTime]::UtcNow.ToString('o');game_process_id=$gameId;connections=$connections})
        } catch {
            $metadataFailed = $true
            throw
        }
    }
} catch {
    $failure = $_.Exception.Message
    Write-Warning $failure
} finally {
    if ($startAttempted) {
        # Target ONLY this previously unused unique name, even after uncertain
        # startup. Never issue an unnamed stop or touch another session's filters.
        try {
            & netsh.exe trace stop ('sessionname=' + $sessionName) | Out-Host
            $stopExit = $LASTEXITCODE
            $stopped = $stopExit -eq 0
            $stoppedAt = [DateTime]::UtcNow.ToString('o')
        } catch { $failure = 'Named-session stop failed: ' + $_.Exception.Message }
        if (-not $stopped) { Write-Warning ('Stop was not confirmed. Inspect only session ' + $sessionName + '; do not issue a global stop.') }
    }
    $report = [ordered]@{
        schema_version=1; plan=$plan; session_name=$sessionName
        started=$started; stopped=$stopped; start_attempted=$startAttempted
        start_exit_code=$startExit; stop_exit_code=$stopExit
        started_at_utc=$startedAt; stopped_at_utc=$stoppedAt
        game_process_ids_observed=$observedIds
        observations=$observations.ToArray(); metadata_check_failed=$metadataFailed
        failure=$failure; etl_path=$etlPath
        every_packet_process_ownership_verified=$false
        capture_scope_verified=$false; handshake_verified=$false; career_access_verified=$false
        network_requests_sent_by_helper=$false
    }
    $report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    if ($stopped) { Write-Host 'NETSH CAPTURE STOPPED. Keep files local; packet scope and Career decoding still need verification.' }
    Write-Host ('Local capture folder: ' + $folder)
    $null = Stop-Transcript
}
if (-not $started -or -not $stopped -or $failure) { throw 'Capture did not complete cleanly. See the local report; do not automatically retry.' }
