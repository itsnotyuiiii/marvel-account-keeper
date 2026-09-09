# Offline regression checks for the actual capture script's filter-list guard.
# Parse its syntax; never execute the capture script or change Packet Monitor.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot '..\scripts\capture-career.ps1'
$parseTokens = $null
$parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$guard = $tree.Find({ param($node)
    $node -is [System.Management.Automation.Language.BinaryExpressionAst] -and
    $node.Operator -eq 'Inotmatch' -and
    $node.Left.Extent.Text -eq '$existingFilters'
}, $true)
if ($null -eq $guard) { throw 'Could not locate the filter-list guard.' }
$pattern = $guard.Right.Value
$cases = @(
    @{ Text = "Packet Filters:`r`nNone"; Empty = $true },
    @{ Text = "Packet Filters:`n    None"; Empty = $true },
    @{ Text = 'None'; Empty = $true },
    @{ Text = 'No filters.'; Empty = $true },
    @{ Text = "  Packet Filters:`r`nNone`r`n  "; Empty = $true },
    @{ Text = ''; Empty = $false },
    @{ Text = 'Packet Filters:'; Empty = $false },
    @{ Text = "Packet Filters:`r`nMAKCareer1 TCP 443"; Empty = $false },
    @{ Text = "Packet Filters:`r`nNone`r`nUnexpected filter"; Empty = $false },
    @{ Text = 'Failed to communicate with the PktMon driver: Access is denied.'; Empty = $false }
)
foreach ($case in $cases) {
    if (($case.Text.Trim() -match $pattern) -ne $case.Empty) {
        throw "Filter-list guard regression: $($case.Text)"
    }
}
Write-Host "Passed $($cases.Count) filter-list checks; capture script syntax is valid. No capture started."
