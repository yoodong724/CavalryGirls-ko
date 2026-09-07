[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageDir,
    [Parameter(Mandatory = $true)][string]$SourceGameDir,
    [Parameter(Mandatory = $true)][string]$OutputDir
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$global:OutputEncoding = $utf8
$approved = @('CavalryGirls_Data/resources.assets', 'CavalryGirls_Data/resources.assets.resS')
$package = [IO.Path]::GetFullPath($PackageDir)
$sourceRoot = [IO.Path]::GetFullPath($SourceGameDir)
$output = [IO.Path]::GetFullPath($OutputDir)
if (-not [IO.Directory]::Exists($package) -or -not [IO.Directory]::Exists($sourceRoot)) { throw 'PackageDir and SourceGameDir must exist.' }
if ([IO.Directory]::Exists($output) -or [IO.File]::Exists($output)) { throw 'OutputDir must be a fresh path.' }
$sourcePrefix = $sourceRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$packagePrefix = $package.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if ($output.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith($packagePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'OutputDir must be outside source and package directories.' }
$success = $false
try {
    $manifestPath = Join-Path $package 'package-manifest.json'
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.runtime_verified -ne $false -or $manifest.release_scope -ne 'stage8_local_candidate' -or @($manifest.targets).Count -ne 2) { throw 'Package contract mismatch.' }
    $paths = @($manifest.targets | ForEach-Object { [string]$_.game_path })
    if ((@($paths | Sort-Object) -join '|') -ne (@($approved | Sort-Object) -join '|')) { throw 'Package target paths are not the approved pair.' }
    if ([string]$manifest.decoder.path -ne 'bin/xdelta3.exe') { throw 'Decoder path is not the fixed package path.' }
    $decoder = Join-Path $package ([string]$manifest.decoder.path)
    if (-not [IO.File]::Exists($decoder) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $decoder).Hash.ToLowerInvariant() -ne $manifest.decoder.sha256) { throw 'Decoder hash mismatch.' }
    [IO.Directory]::CreateDirectory($output) | Out-Null
    $results = @()
    foreach ($target in @($manifest.targets)) {
        if ([string]$target.delta -ne ([IO.Path]::GetFileName([string]$target.game_path) + '.xdelta3')) { throw "Delta path mismatch: $($target.game_path)" }
        $source = Join-Path $sourceRoot ([string]$target.game_path)
        $delta = Join-Path $package ([string]$target.delta)
        if (-not [IO.File]::Exists($source) -or (Get-Item $source).Length -ne [int64]$target.source_size -or (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant() -ne $target.source_sha256) { throw "Source hash/size mismatch: $($target.game_path)" }
        if (-not [IO.File]::Exists($delta) -or (Get-Item $delta).Length -ne [int64]$target.delta_size -or (Get-FileHash -Algorithm SHA256 -LiteralPath $delta).Hash.ToLowerInvariant() -ne $target.delta_sha256) { throw "Delta hash/size mismatch: $($target.game_path)" }
        $decoded = Join-Path $output ([IO.Path]::GetFileName([string]$target.game_path))
        & $decoder '-f' '-d' '-s' $source $delta $decoded
        if ($LASTEXITCODE -ne 0) { throw "Windows decoder failed: $($target.game_path)" }
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $decoded).Hash.ToLowerInvariant()
        $size = (Get-Item $decoded).Length
        if ($hash -ne $target.built_sha256 -or $size -ne [int64]$target.built_size) { throw "Decoded output mismatch: $($target.game_path)" }
        $results += [ordered]@{ path=$target.game_path; sha256=$hash; size=$size; passed=$true; verification='fresh_windows_full_decode' }
        Write-Output "PASS: $($target.game_path)"
    }
    $evidence = [ordered]@{ targets=$results; game_executed=$false; game_installed=$false; source_read_only=$true; decoder_sha256=$manifest.decoder.sha256; package_manifest_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() }
    [IO.File]::WriteAllText((Join-Path $output 'verification.json'), ($evidence | ConvertTo-Json -Depth 10), $utf8)
    $success = $true
    Write-Output 'PASS: both deltas freshly decoded and verified; no game installed or launched.'
} finally {
    if (-not $success -and [IO.Directory]::Exists($output)) { [IO.Directory]::Delete($output, $true) }
}
