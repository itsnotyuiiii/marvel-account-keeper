# Preview a bounded, handshake-first capture of already observed game peers.
# Unlike capture-career.ps1, this omits the local ephemeral TCP port so it can
# capture the first bytes of a replacement connection to those SAME peers.
# It can also capture another local process using those exact peers. Obtain
# explicit approval for that scope before using -Record. No self-elevation.
[CmdletBinding()]
param(
    [switch]$Record,
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$ExpectedGameProcessId,
    [ValidateRange(1,90)][int]$DurationSeconds = 90,
    [switch]$AllowGameRestart,
    [string]$PriorCaptureReport,
    [ValidatePattern('^[0-9a-f]{64}$')][string]$ReviewedScopeFingerprint
)

$ErrorActionPreference = 'Stop'

# The 2026-09-06 dc9ddd76 capture contained packets outside the configured
# three peer endpoints. Keep previews available, but do not repeat live PktMon
# recording until the discrepancy is understood and scope enforcement verified.
if ($Record) { # LIVE_CAPTURE_SCOPE_HOLD
    throw 'Live handshake capture is temporarily disabled after an observed filter-scope mismatch. Preview only; no settings changed.'
}

function Get-HandshakeConnections {
    param([int]$GameProcessId)
    Get-NetTCPConnection -OwningProcess $GameProcessId -State Established |
        Where-Object { $_.RemoteAddress -notin @('0.0.0.0','::') -and
            -not [Net.IPAddress]::IsLoopback([Net.IPAddress]::Parse($_.RemoteAddress)) } |
        Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort
}

function Get-HandshakePeerKey {
    param($Peer)
    '{0}|{1}|{2}' -f $Peer.LocalAddress,$Peer.RemoteAddress,$Peer.RemotePort
}

function Get-HandshakePacketCount {
    param([string]$Path)
    try {
        $count = 0
        $recognized = $false
        Get-WinEvent -Path $Path -Oldest -ErrorAction Stop | ForEach-Object {
            if ($_.ProviderName -eq 'Microsoft-Windows-PktMon') {
                $recognized = $true
                if ($_.Id -in @(160,170)) { $count++ }
            }
        }
        if ($recognized) { return [pscustomobject]@{verified=$true; count=$count} }
    } catch { }
    [pscustomobject]@{verified=$false; count=$null}
}

$game = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
$initial = @()
if ($PriorCaptureReport) {
    # Reuse the identical approved peers while the user is at the launcher.
    # Never pretend that a historical PID/connection is currently running.
    if (-not $AllowGameRestart -or $game.Count -ne 0 -or -not $ReviewedScopeFingerprint) {
        throw 'Prior-report mode requires no running game, restart mode and the previously reviewed fingerprint. Nothing was captured.'
    }
    $diagnosticsRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MarvelAccountKeeper\diagnostics')) + [IO.Path]::DirectorySeparatorChar
    $priorPath = [IO.Path]::GetFullPath($PriorCaptureReport)
    if (-not $priorPath.StartsWith($diagnosticsRoot,[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($priorPath) -ne 'capture-report.json' -or
        (Get-Item -LiteralPath $priorPath).Length -gt 1048576) {
        throw 'Prior report must be a bounded local capture report in the diagnostics directory. Nothing was captured.'
    }
    $prior = Get-Content -LiteralPath $priorPath -Raw | ConvertFrom-Json
    $priorStopped = [DateTimeOffset]::Parse($prior.stopped_at_utc)
    $now = [DateTimeOffset]::UtcNow
    if ($prior.schema_version -notin @(1,2) -or
        $prior.capture_kind -ne 'handshake_first_observed_remote_peers' -or
        $priorStopped -gt $now -or $priorStopped -lt $now.AddMinutes(-30) -or
        $prior.plan.expected_game_process_id -ne $ExpectedGameProcessId -or
        $prior.plan.duration_seconds -ne $DurationSeconds -or
        $prior.plan.keeps_recording_same_fixed_peers_across_user_game_restart -ne $true -or
        $prior.plan.scope_fingerprint -cne $ReviewedScopeFingerprint) {
        throw 'Prior report must be recent and match the previously reviewed restart scope. Nothing was captured.'
    }
    $localAddresses = @(Get-NetIPAddress -ErrorAction Stop | Select-Object -ExpandProperty IPAddress)
    $peers = @($prior.plan.peer_filters | ForEach-Object {
        $localIp = [Net.IPAddress]::Parse($_.LocalAddress)
        $remoteIp = [Net.IPAddress]::Parse($_.RemoteAddress)
        if ($localIp.ToString() -notin $localAddresses -or
            [Net.IPAddress]::IsLoopback($localIp) -or [Net.IPAddress]::IsLoopback($remoteIp) -or
            $localIp.ToString() -in @('0.0.0.0','::') -or $remoteIp.ToString() -in @('0.0.0.0','::') -or
            $_.RemotePort -notin @(10200,12101,21308)) {
            throw 'Prior report contains an unavailable local address or unsupported peer. Nothing was captured.'
        }
        [pscustomobject]@{LocalAddress=$localIp.ToString();RemoteAddress=$remoteIp.ToString();RemotePort=[int]$_.RemotePort}
    } | Sort-Object LocalAddress,RemoteAddress,RemotePort -Unique)
} else {
    if ($game.Count -ne 1 -or $game[0].Id -ne $ExpectedGameProcessId) {
        throw 'The expected game process is not the only running game. Nothing was captured.'
    }
    $initial = @(Get-HandshakeConnections -GameProcessId $ExpectedGameProcessId)
    # Only the three candidate non-HTTPS ports actually observed in this task.
    $peers = @($initial | Where-Object { $_.RemotePort -in @(10200,12101,21308) } |
        Select-Object LocalAddress,RemoteAddress,RemotePort |
        Sort-Object LocalAddress,RemoteAddress,RemotePort -Unique)
}
# Never add guessed hosts, all-port filters, subnets or an all-application 443 filter.
if ($peers.Count -lt 1 -or $peers.Count -gt 6) {
    throw 'Expected 1-6 already observed game peers on ports 10200, 12101 or 21308. Nothing was captured.'
}
$peerKeys = @($peers | ForEach-Object { Get-HandshakePeerKey $_ })
$scopeParts = @('v1|{0}|{1}|{2}' -f $ExpectedGameProcessId,$DurationSeconds,[bool]$AllowGameRestart) + $peerKeys
$scopeHasher = [Security.Cryptography.SHA256]::Create()
try {
    $scopeBytes = [Text.Encoding]::UTF8.GetBytes([string]::Join([char]10,$scopeParts))
    $scopeFingerprint = [BitConverter]::ToString($scopeHasher.ComputeHash($scopeBytes)).Replace('-','').ToLowerInvariant()
} finally { $scopeHasher.Dispose() }
if ($PriorCaptureReport -and $scopeFingerprint -cne $ReviewedScopeFingerprint) {
    throw 'Prior report endpoints do not match the explicitly reviewed fingerprint. Nothing was captured.'
}
$plan = [ordered]@{
    mode = $(if ($Record) { 'record_requested' } else { 'preview_only' })
    expected_game_process_id = $ExpectedGameProcessId
    duration_seconds = $DurationSeconds
    capture_components = 'all'
    peer_filters = $peers
    peer_source = $(if ($PriorCaptureReport) { 'recent_reviewed_report' } else { 'current_game_connections' })
    initial_game_process_present = ($game.Count -eq 1)
    local_tcp_port_restricted = $false
    other_local_processes_using_same_peers_may_be_captured = $true
    new_remote_peers_added = $false
    changes_game_or_login = $false
    keeps_recording_same_fixed_peers_across_user_game_restart = [bool]$AllowGameRestart
    record_requires_explicit_scope_approval = $true
    scope_fingerprint = $scopeFingerprint
}
$plan | ConvertTo-Json -Depth 5 | Write-Output
if (-not $Record) { return }
# This pins the reviewed endpoints/duration/PID; it does NOT replace user consent.
if (-not $ReviewedScopeFingerprint -or $ReviewedScopeFingerprint -cne $scopeFingerprint) {
    throw 'Recording requires the unchanged preview scope fingerprint and explicit scope approval. No settings changed.'
}

$captureIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$capturePrincipal = [Security.Principal.WindowsPrincipal]::new($captureIdentity)
if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell is required. This helper does not elevate itself.'
}
$sessions = (& logman.exe query -ets 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $sessions -match '(?im)^\s*PktMon\b') {
    throw 'Cannot confirm that Packet Monitor is idle. No capture settings were changed.'
}
$filters = (& pktmon.exe filter list 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $filters -notmatch '(?i)\A(?:Packet Filters:\s*)?(?:No filters\.?|None\.?)\z') {
    throw 'Expected an empty Packet Monitor filter list. Existing filters were left untouched.'
}

$runId = [guid]::NewGuid().ToString('N')
$folder = Join-Path $env:LOCALAPPDATA ('MarvelAccountKeeper\diagnostics\career-handshake-' + $runId)
$null = New-Item -ItemType Directory -Path $folder
$etl = Join-Path $folder 'career.etl'
$pcap = Join-Path $folder 'career.pcapng'
$reportPath = Join-Path $folder 'capture-report.json'
$ownedFilters = [Collections.Generic.List[string]]::new()
$filterSnapshot = $null
$observations = [Collections.Generic.List[object]]::new()
$outsidePeers = @{}
$started = $false
$startAttempted = $false
$stopped = $false
$cleanupFailed = $false
$gameChanged = [bool]$PriorCaptureReport
$processIdsObserved = @($game | Select-Object -ExpandProperty Id)
$metadataCheckFailed = $false
$startedAt = $null
$endedAt = $null
try {
    $index = 0
    foreach ($peer in $peers) {
        $index++
        foreach ($orientation in @('Out','In')) {
            $filterName = 'MAKHello{0}{1}{2}' -f $runId.Substring(0,8),$index,$orientation
            # Track even a failed attempt so partial setup enters cleanup checks.
            $ownedFilters.Add($filterName)
            $firstIp = if ($orientation -eq 'Out') { $peer.LocalAddress } else { $peer.RemoteAddress }
            $secondIp = if ($orientation -eq 'Out') { $peer.RemoteAddress } else { $peer.LocalAddress }
            & pktmon.exe filter add $filterName -t TCP -i $firstIp $secondIp -p $peer.RemotePort | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Could not add an exact game-peer filter. No broader filter was used.' }
            $filterSnapshot = (& pktmon.exe filter list 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                $filterSnapshot = $null
                throw 'Could not verify the temporary filter list. Inspect Packet Monitor before retrying.'
            }
        }
    }
    $startAttempted = $true
    & pktmon.exe start --capture --comp all --pkt-size 0 --file-size 32 --log-mode circular --file-name $etl | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Packet Monitor did not start.' }
    $started = $true
    $startedAt = [DateTime]::UtcNow.ToString('o')
    if ($PriorCaptureReport) {
        Write-Host 'HANDSHAKE CAPTURE READY: press Launch now, use the same account, then open your own Career.' -ForegroundColor Green
    } else { Write-Host 'HANDSHAKE CAPTURE READY: reconnect using the same game account, then open your own Career.' -ForegroundColor Green }
    if ($AllowGameRestart) {
        Write-Host 'A normal user-controlled game restart is allowed. The same fixed endpoint filters stay active; no new peers are added.'
    } else { Write-Host 'Do not close the game process: only an in-game return-to-title/login flow is supported in this mode.' }
    Write-Host 'Do not change accounts, launch a match, or close this terminal. This helper will not close, launch or log in to the game.'
    Write-Host "Stops automatically within $DurationSeconds seconds. New remote peers are NOT added."
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $DurationSeconds) {
        $remaining = [int][Math]::Max(1,[Math]::Ceiling(1000*$DurationSeconds-$timer.Elapsed.TotalMilliseconds))
        Start-Sleep -Milliseconds ([Math]::Min(500,$remaining))
        $current = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
        if ($current.Count -ne 1 -or $current[0].Id -ne $ExpectedGameProcessId) {
            $gameChanged = $true
            if (-not $AllowGameRestart -or $current.Count -gt 1) { break }
            if ($current.Count -eq 0) {
                $observations.Add([pscustomobject]@{at_utc=[DateTime]::UtcNow.ToString('o'); game_process_id=$null; connections=@()})
                continue
            }
        }
        $observedProcessId = $current[0].Id
        if ($observedProcessId -notin $processIdsObserved) { $processIdsObserved += $observedProcessId }
        try {
            # A replacement PID is used for endpoint metadata only. It never
            # changes the reviewed packet filters or proves packet ownership.
            $connections = @(Get-HandshakeConnections -GameProcessId $observedProcessId)
            $observations.Add([pscustomobject]@{at_utc=[DateTime]::UtcNow.ToString('o'); game_process_id=$observedProcessId; connections=$connections})
            foreach ($connection in $connections) {
                $key = Get-HandshakePeerKey $connection
                if ($key -notin $peerKeys) { $outsidePeers[$key] = $true }
            }
        } catch { $metadataCheckFailed = $true }
    }
} finally {
    if ($started) {
        & pktmon.exe stop | Out-Host
        $stopped = $LASTEXITCODE -eq 0
        $endedAt = [DateTime]::UtcNow.ToString('o')
        if (-not $stopped) { Write-Warning 'Automatic stop failed. Run pktmon stop in Administrator PowerShell now.' }
    }
    if (-not $startAttempted -or $stopped) {
        if ($ownedFilters.Count) {
            # This installed CLI supports removing ALL filters only. The entry
            # guard required none; keep filters if the post-setup list changed.
            $currentFilters = (& pktmon.exe filter list 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $null -ne $filterSnapshot -and $currentFilters -ceq $filterSnapshot) {
                & pktmon.exe filter remove | Out-Host
                if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
            } else { $cleanupFailed = $true }
        }
        if ($cleanupFailed) { Write-Warning 'Filter cleanup was not completed: the list changed or could not be verified/removed. Filters were not blindly cleared; inspect Packet Monitor before retrying.' }
    } elseif (-not $started) {
        Write-Warning 'Capture startup could not be verified. Narrow MAKHello filters were retained; inspect Packet Monitor before cleanup or retry.'
    }
}
if ($stopped) {
    & pktmon.exe etl2pcap $etl --out $pcap | Out-Host
    $converted = $LASTEXITCODE -eq 0
    $packetCount = Get-HandshakePacketCount -Path $etl
    $report = [ordered]@{
        schema_version = 2
        capture_kind = 'handshake_first_observed_remote_peers'
        plan = $plan
        initial_game_connections = $initial
        prior_capture_report = $PriorCaptureReport
        started_at_utc = $startedAt
        stopped_at_utc = $endedAt
        packet_event_count = $packetCount.count
        packet_count_verified = $packetCount.verified
        pcap_converted = $converted
        temporary_filter_cleanup_failed = $cleanupFailed
        game_changed = $gameChanged
        game_process_ids_observed = $processIdsObserved
        metadata_check_failed = $metadataCheckFailed
        outside_peer_count_observed = $outsidePeers.Count
        observations = $observations.ToArray()
        every_packet_process_ownership_verified = $false
        handshake_verified = $false
        career_access_verified = $false
        network_requests_sent_by_helper = $false
    }
    $report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host 'HANDSHAKE CAPTURE STOPPED. Keep the files local; handshake/API decoding is not yet verified.' -ForegroundColor Green
    Write-Host "Local capture folder: $folder"
}
