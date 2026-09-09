# Capture a single user-authorized Career lookup, without changing game files,
# certificates, proxy settings, or TLS. Raw captures may contain session data.
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$TlsOnly,
    [switch]$AllComponents,
    [switch]$FollowConnections,
    [ValidateRange(0, 2147483647)][int]$ExpectedGameProcessId = 0
)

$ErrorActionPreference = 'Stop'

function Get-CareerConnectionKey {
    param($Connection)
    '{0}|{1}|{2}|{3}' -f $Connection.LocalAddress, $Connection.LocalPort,
        $Connection.RemoteAddress, $Connection.RemotePort
}

function Get-CareerConnectionSnapshot {
    param([int]$GameProcessId)
    # Only endpoint metadata belonging to the selected game, never packet data.
    Get-NetTCPConnection -OwningProcess $GameProcessId -State Established |
        Where-Object { $_.RemoteAddress -notin @('0.0.0.0', '::') -and
            -not [Net.IPAddress]::IsLoopback([Net.IPAddress]::Parse($_.RemoteAddress)) } |
        Sort-Object LocalAddress, LocalPort, RemoteAddress, RemotePort -Unique |
        Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort
}

function Add-CareerConnectionFilters {
    param($Connection, [int]$Number)
    # CLI help describes unordered pairs, but the Win32 constraint API names
    # source/destination fields. State both orientations explicitly. If the
    # implementation is unordered, these are redundant, not broader filters.
    & pktmon.exe filter add "MAKCareer${Number}Out" -t TCP -i $Connection.LocalAddress $Connection.RemoteAddress -p $Connection.LocalPort $Connection.RemotePort | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Could not add the outgoing game-connection filter.' }
    & pktmon.exe filter add "MAKCareer${Number}In" -t TCP -i $Connection.RemoteAddress $Connection.LocalAddress -p $Connection.RemotePort $Connection.LocalPort | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Could not add the incoming game-connection filter.' }
}

function Get-CareerPacketEventCount {
    param([string]$CapturePath)
    # Count ETL packet snapshots, not unique wire packets. Never format events:
    # their messages/properties can contain the complete captured packet.
    try {
        $eventCount = 0
        $providerObserved = $false
        # On this Windows ETL reader, FilterHashtable's provider/ID filter
        # returns NoMatchingEventsFound even when -Path enumerates 18 packets.
        # Enumerate the bounded local trace and filter its decoded metadata.
        Get-WinEvent -Path $CapturePath -Oldest -ErrorAction Stop | ForEach-Object {
            if ($_.ProviderName -eq 'Microsoft-Windows-PktMon') {
                $providerObserved = $true
                if ($_.Id -in @(160, 170)) { $eventCount++ }
            }
        }
        if ($providerObserved) {
            [pscustomobject]@{ verified = $true; count = $eventCount }
        } else {
            [pscustomobject]@{ verified = $false; count = $null }
        }
    } catch {
        # An unreadable or undecoded log is not evidence of an empty capture.
        [pscustomobject]@{ verified = $false; count = $null }
    }
}

$gameProcesses = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
if ($gameProcesses.Count -ne 1) {
    throw 'Expected exactly one running Marvel Rivals game. Log in and stay in the lobby.'
}
if ($ExpectedGameProcessId -and $gameProcesses[0].Id -ne $ExpectedGameProcessId) {
    throw 'The game process changed. Nothing was captured; check the diagnostic session before retrying.'
}
$initialConnections = @(Get-CareerConnectionSnapshot -GameProcessId $gameProcesses[0].Id)
$connections = @($initialConnections | Where-Object { -not $TlsOnly -or $_.RemotePort -eq 443 })
$captureComponents = if ($AllComponents) { 'all' } else { 'nics' }
if (($connections.Count -lt 1 -and -not $FollowConnections) -or $connections.Count -gt 16) {
    throw 'Expected 1-16 established game TCP connections (two filters each). Nothing was captured.'
}
if ($DryRun) {
    Write-Host "Dry run: found $($connections.Count) game TCP connections. No capture or system changes made."
    Write-Host "Excluded established remote game connections: $($initialConnections.Count - $connections.Count)."
    Write-Host "Capture components: $captureComponents; exact game endpoint filters are unchanged."
    Write-Host "Explicit forward/reverse filters: $($connections.Count * 2); follow new game connections: $([bool]$FollowConnections)."
    if ($connections.Count -eq 0) {
        Write-Host 'Would wait up to 30 seconds for an eligible game connection before adding filters or starting capture.'
    }
    return
}
$captureIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$capturePrincipal = [Security.Principal.WindowsPrincipal]::new($captureIdentity)
if (-not $capturePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as Administrator and run this script there. It does not elevate itself.'
}

$waitedForInitialConnection = $false
if ($connections.Count -eq 0) {
    $waitedForInitialConnection = $true
    Write-Host 'WAITING FOR GAME CONNECTION: open your own Career and switch a statistics/history tab now.' -ForegroundColor Yellow
    Write-Host 'Waiting up to 30 seconds for an eligible connection. Recording has NOT started.'
    if ($TlsOnly) { Write-Host 'Only game-owned HTTPS (remote port 443) connections qualify.' }
    $connectionWaitTimer = [Diagnostics.Stopwatch]::StartNew()
    while ($connections.Count -eq 0 -and $connectionWaitTimer.Elapsed.TotalSeconds -lt 30) {
        Start-Sleep -Milliseconds 500
        $currentGame = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
        if ($currentGame.Count -ne 1 -or $currentGame[0].Id -ne $gameProcesses[0].Id) {
            throw 'The game process changed while waiting. Nothing was captured.'
        }
        $initialConnections = @(Get-CareerConnectionSnapshot -GameProcessId $gameProcesses[0].Id)
        $connections = @($initialConnections | Where-Object { -not $TlsOnly -or $_.RemotePort -eq 443 })
    }
    if ($connections.Count -eq 0) {
        throw 'No eligible game connection appeared within 30 seconds. No filters or capture were started.'
    }
    if ($connections.Count -gt 16) {
        throw 'Expected 1-16 established game TCP connections (two filters each). Nothing was captured.'
    }
}

# Do not interrupt another diagnostic session or broaden somebody else's filters.
# Recheck after any initial wait, before creating a folder or changing filters.
$sessions = (& logman.exe query -ets 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $sessions -match '(?im)^\s*PktMon\b') {
    throw 'Cannot confirm that Packet Monitor is idle. No capture settings were changed.'
}
$existingFilters = (& pktmon.exe filter list 2>&1 | Out-String).Trim()
# Windows can prefix the empty-list marker with a "Packet Filters:" heading.
# Anchor the entire output so a real filter or an error cannot pass this check.
if ($LASTEXITCODE -ne 0 -or $existingFilters -notmatch '(?i)\A(?:Packet Filters:\s*)?(?:No filters\.?|None\.?)\z') {
    Write-Host $existingFilters
    throw 'Expected an empty Packet Monitor filter list. Existing settings were left untouched; show Codex the output above.'
}

$captureFolder = Join-Path $env:LOCALAPPDATA ('MarvelAccountKeeper\diagnostics\career-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $captureFolder
$capturePath = Join-Path $captureFolder 'career.etl'
$pcapPath = Join-Path $captureFolder 'career.pcapng'
$reportPath = Join-Path $captureFolder 'capture-report.json'
$selectedKeys = @($connections | ForEach-Object { Get-CareerConnectionKey $_ })
$initiallyExcluded = $initialConnections.Count - $connections.Count
$addedConnectionCount = 0
$scopeRefreshFailure = $null
$sampleMilliseconds = if ($FollowConnections) { 500 } else { 5000 }
$unselectedKeys = @{}
$missingSelectedKeys = @{}
$observations = [Collections.Generic.List[object]]::new()
$observations.Add([pscustomobject]@{ at_utc = [DateTime]::UtcNow.ToString('o'); connections = $initialConnections })
$connectionCheckFailed = $false
$gameChanged = $false
$started = $false
$filtersAdded = $false
$stopped = $false
try {
    $index = 0
    foreach ($connection in $connections) {
        # Both IPs and TCP ports must match. No broad port-443 capture.
        $index++
        # Also clean up a partially added pair if its second orientation fails.
        $filtersAdded = $true
        Add-CareerConnectionFilters -Connection $connection -Number $index
    }
    & pktmon.exe start --capture --comp $captureComponents --pkt-size 0 --file-size 32 --log-mode circular --file-name $capturePath
    if ($LASTEXITCODE -ne 0) { throw 'Packet Monitor did not start.' }
    $started = $true
    Write-Host ''
    Write-Host 'CAPTURE READY: switch to the game and open your own Career profile once.' -ForegroundColor Green
    Write-Host 'Switch a statistics/history tab. Do not log out, launch a match, or close this terminal.'
    if ($waitedForInitialConnection) {
        Write-Host 'The initial connection was detected. Repeat the Career/tab change now; the earlier request may have been missed.'
    }
    Write-Host "Selected $($connections.Count) existing game TCP connections, with forward and reverse filters."
    if ($FollowConnections) {
        Write-Host 'Following newly established connections of this same game process, up to 16 total.'
        Write-Host 'Polling can miss short-lived connections and their first packets/handshake; coverage is not guaranteed.'
    } else {
        Write-Host 'New connections are NOT added.'
    }
    if ($AllComponents) {
        Write-Host 'Capturing those same connections across the network stack, not just the Wi-Fi adapter.'
    }
    if ($TlsOnly) {
        Write-Host "Port-443 restriction excludes $($initialConnections.Count - $connections.Count) other established game connections."
    }
    Write-Host 'This does not guarantee a TLS handshake or Career traffic. Keep the diagnostic game session open.'
    Write-Host 'Capture stops automatically in 30 seconds.'
    $captureTimer = [Diagnostics.Stopwatch]::StartNew()
    while ($captureTimer.Elapsed.TotalSeconds -lt 30) {
        $remainingMilliseconds = [int][Math]::Max(1, [Math]::Ceiling(30000 - $captureTimer.Elapsed.TotalMilliseconds))
        Start-Sleep -Milliseconds ([Math]::Min($sampleMilliseconds, $remainingMilliseconds))
        $currentGame = @(Get-Process -Name 'Marvel-Win64-Shipping' -ErrorAction SilentlyContinue)
        if ($currentGame.Count -ne 1 -or $currentGame[0].Id -ne $gameProcesses[0].Id) {
            $gameChanged = $true
            Write-Warning 'The selected game exited or changed. Stopping this capture.'
            break
        }
        try {
            $snapshot = @(Get-CareerConnectionSnapshot -GameProcessId $gameProcesses[0].Id)
            $observations.Add([pscustomobject]@{ at_utc = [DateTime]::UtcNow.ToString('o'); connections = $snapshot })
            $currentKeys = @($snapshot | ForEach-Object { Get-CareerConnectionKey $_ })
            foreach ($key in $currentKeys) {
                if ($key -notin $selectedKeys) { $unselectedKeys[$key] = $true }
            }
            foreach ($key in $selectedKeys) {
                if ($key -notin $currentKeys) { $missingSelectedKeys[$key] = $true }
            }
        } catch {
            $connectionCheckFailed = $true
            continue
        }
        if ($FollowConnections -and $captureTimer.Elapsed.TotalSeconds -lt 30) {
            foreach ($connection in $snapshot) {
                $key = Get-CareerConnectionKey $connection
                if ($key -in $selectedKeys -or ($TlsOnly -and $connection.RemotePort -ne 443)) { continue }
                if ($connections.Count -ge 16) {
                    $scopeRefreshFailure = 'connection_filter_limit_reached'
                    break
                }
                try {
                    Add-CareerConnectionFilters -Connection $connection -Number ($index + 1)
                } catch {
                    $scopeRefreshFailure = 'connection_filter_refresh_failed'
                    break
                }
                $index++
                $connections += $connection
                $selectedKeys += $key
                $addedConnectionCount++
            }
            if ($scopeRefreshFailure) {
                Write-Warning "Stopping capture: $scopeRefreshFailure. Existing captured data will be kept."
                break
            }
        }
    }
}
finally {
    if ($started) {
        & pktmon.exe stop
        $stopped = $LASTEXITCODE -eq 0
        if (-not $stopped) { Write-Warning 'Automatic stop failed. Run pktmon stop in this Administrator window now.' }
    }
    if ($filtersAdded -and (-not $started -or $stopped)) {
        # Entry required zero filters, so these were created only by this script.
        & pktmon.exe filter remove
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not remove the temporary MAKCareer filters.' }
    }
}
if ($stopped) {
    & pktmon.exe etl2pcap $capturePath --out $pcapPath
    $converted = $LASTEXITCODE -eq 0
    if (-not $converted) { Write-Warning 'PCAP conversion failed; the original ETL remains available locally.' }
    $packetCount = Get-CareerPacketEventCount -CapturePath $capturePath
    $outcome = 'packet_count_unverified'
    if ($packetCount.verified) {
        if ($packetCount.count -eq 0) {
            $outcome = 'no_packets_captured'
            Write-Warning 'NO PACKETS CAPTURED: there is nothing to decrypt. This is not a UID or authentication failure.'
        } else {
            $outcome = 'packet_snapshots_captured_not_decoded'
            Write-Host "Logged packet snapshots: $($packetCount.count). API/TLS decoding has not been verified."
        }
    } else {
        Write-Warning 'Could not verify the packet count. The ETL remains available for inspection.'
    }
    $report = [ordered]@{
        schema_version = 4
        game_process_id = $gameProcesses[0].Id
        tls_only = [bool]$TlsOnly
        capture_components = $captureComponents
        explicit_reverse_filters = $true
        follow_connections = [bool]$FollowConnections
        waited_for_initial_connection = $waitedForInitialConnection
        connections_added_during_capture = $addedConnectionCount
        scope_refresh_failure = $scopeRefreshFailure
        outcome = $outcome
        packet_event_count = $packetCount.count
        packet_count_verified = $packetCount.verified
        pcap_converted = $converted
        selected_connections = $connections
        initially_excluded_connections = $initiallyExcluded
        unselected_connections_observed = $unselectedKeys.Count
        selected_connections_later_missing = $missingSelectedKeys.Count
        connection_check_failed = $connectionCheckFailed
        game_changed = $gameChanged
        observations = $observations.ToArray()
        new_connections_captured = $(if ($FollowConnections) { $null } else { $false })
        new_connection_coverage_guaranteed = $false
        career_access_verified = $false
    }
    try {
        $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    } catch {
        Write-Warning 'Could not save the endpoint/count report; the capture files remain available.'
    }
    Write-Host "Unselected game connections observed: $($unselectedKeys.Count); selected connections later missing: $($missingSelectedKeys.Count)."
    Write-Host "New game connections added to filters: $addedConnectionCount."
    Write-Host ''
    Write-Host 'CAPTURE STOPPED. Return to Codex and say done.' -ForegroundColor Green
    Write-Host "Local capture folder: $captureFolder"
    Write-Host 'Do not upload or paste the capture. It may contain session data.'
}
