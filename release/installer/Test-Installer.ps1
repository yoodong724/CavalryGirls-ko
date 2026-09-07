[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Install-KoreanPatch.ps1')

$passed = 0
function Assert-True([bool]$Condition, [string]$Name) {
    if (-not $Condition) { throw "ASSERT FAILED: $Name" }
    $script:passed++
    Write-Host "PASS: $Name"
}
function Write-Bytes([string]$Path, [byte[]]$Bytes) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    [IO.File]::WriteAllBytes($Path, $Bytes)
}
function New-Game([string]$Path, [byte[]]$First, [byte[]]$Second) {
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    Write-Bytes (Join-Path $Path 'CavalryGirls.exe') ([byte[]](1,2,3))
    Write-Bytes (Join-Path $Path 'CavalryGirls_Data\resources.assets') $First
    Write-Bytes (Join-Path $Path 'CavalryGirls_Data\resources.assets.resS') $Second
}
function New-Package([string]$Path, [byte[][]]$Sources, [byte[][]]$Built) {
    [IO.Directory]::CreateDirectory((Join-Path $Path 'bin')) | Out-Null
    Write-Bytes (Join-Path $Path 'bin\xdelta3.exe') ([byte[]](77,90,0,1))
    $records = @()
    $hashes = @{}
    for ($i = 0; $i -lt 2; $i++) {
        $relative = $script:TargetPaths[$i]
        $name = [IO.Path]::GetFileName($relative) + '.xdelta3'
        $delta = Join-Path $Path $name
        Write-Bytes $delta $Built[$i]
        $sourceFile = Join-Path $Path ("source-$i.bin")
        $builtFile = Join-Path $Path ("built-$i.bin")
        Write-Bytes $sourceFile $Sources[$i]
        Write-Bytes $builtFile $Built[$i]
        $sourceHash = Get-FileSha256 $sourceFile
        $builtHash = Get-FileSha256 $builtFile
        $hashes[$relative] = $sourceHash
        $records += [ordered]@{
            game_path=$relative; source_sha256=$sourceHash; source_size=[int64]$Sources[$i].Length
            built_sha256=$builtHash; built_size=[int64]$Built[$i].Length
            delta=$name; delta_sha256=(Get-FileSha256 $delta); delta_size=[int64]$Built[$i].Length
        }
    }
    $manifest = [ordered]@{
        schema_version='1.0.0'; source_revision=$script:SourceRevision; runtime_verified=$false
        release_scope='stage8_local_candidate'; backup_directory=$script:BackupName; targets=$records
        decoder=[ordered]@{ path='bin/xdelta3.exe'; sha256=(Get-FileSha256 (Join-Path $Path 'bin\xdelta3.exe')) }
    }
    [IO.File]::WriteAllText((Join-Path $Path 'package-manifest.json'), ($manifest | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    return $hashes
}

$root = Join-Path ([IO.Path]::GetTempPath()) ('cgko-installer-test-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {
    $source = [byte[][]]@(([byte[]](10,11,12)), ([byte[]](20,21,22,23)))
    $built = [byte[][]]@(([byte[]](30,31,32,33)), ([byte[]](40,41,42,43,44)))
    $game = Join-Path $root '게임 폴더'
    New-Game $game $source[0] $source[1]
    $package = Join-Path $game '이름과 버전이 자유로운 패치-r99'
    $hashes = New-Package $package $source $built
    $runner = { param($Decoder,$InputFile,$Delta,$Output) [IO.File]::Copy($Delta, $Output, $true) }

    $previousDirectory = [Environment]::CurrentDirectory
    try {
        [Environment]::CurrentDirectory = [IO.Path]::GetTempPath()
        $resolvedParent = Resolve-GameDirectory $null $package
    } finally { [Environment]::CurrentDirectory = $previousDirectory }
    Assert-True ($resolvedParent -eq [IO.Path]::GetFullPath($game)) 'package parent resolved independent of current directory and package name'

    $overridePackage = Join-Path $root '기술용 외부 package'
    [IO.Directory]::CreateDirectory($overridePackage) | Out-Null
    Assert-True ((Resolve-GameDirectory $game $overridePackage) -eq [IO.Path]::GetFullPath($game)) 'explicit GameDir override retained'

    $tooDeepPackage = Join-Path $game '중간 폴더\패치'
    [IO.Directory]::CreateDirectory($tooDeepPackage) | Out-Null
    $tooDeepRejected = $false
    try { $null = Resolve-GameDirectory $null $tooDeepPackage } catch { $tooDeepRejected = $true }
    Assert-True $tooDeepRejected 'wrong nesting rejected without grandparent fallback'

    $unrelatedPackage = Join-Path $root '게임 밖\패치'
    [IO.Directory]::CreateDirectory($unrelatedPackage) | Out-Null
    $searchFallbackRejected = $false
    try { $null = Resolve-GameDirectory $null $unrelatedPackage } catch { $searchFallbackRejected = $true }
    Assert-True $searchFallbackRejected 'missing parent game rejected without search fallback'

    Invoke-Install $game $package $hashes $runner
    Assert-True ((Get-FileSha256 (Join-Path $game $script:TargetPaths[0])) -eq (Get-FileSha256 (Join-Path $package 'resources.assets.xdelta3'))) 'install output verified'
    Invoke-Install $game $package $hashes $runner
    Assert-True $true 'repeated install is idempotent'
    Invoke-Restore $game $hashes
    Assert-True ((Get-FileSha256 (Join-Path $game $script:TargetPaths[0])) -eq $hashes[$script:TargetPaths[0]]) 'restore output verified'
    Invoke-Restore $game $hashes
    Assert-True $true 'repeated restore is idempotent'

    $deltaPath = Join-Path $package 'resources.assets.xdelta3'
    Write-Bytes $deltaPath ([byte[]](88))
    $deltaRejected = $false
    try { Invoke-Install $game $package $hashes $runner } catch { $deltaRejected = $true }
    $unchangedAfterDeltaReject = (Get-FileSha256 (Join-Path $game $script:TargetPaths[0])) -eq $hashes[$script:TargetPaths[0]]
    Assert-True ($deltaRejected -and $unchangedAfterDeltaReject) 'corrupted delta rejected without game write'
    Write-Bytes $deltaPath $built[0]

    $backupFile = Join-Path $game ('.cavalry-girls-ko-backup\files\' + $script:TargetPaths[0])
    Write-Bytes $backupFile ([byte[]](77))
    $backupRejected = $false
    try { Invoke-Install $game $package $hashes $runner } catch { $backupRejected = $true }
    $unchangedAfterBackupReject = (Get-FileSha256 (Join-Path $game $script:TargetPaths[0])) -eq $hashes[$script:TargetPaths[0]]
    Assert-True ($backupRejected -and $unchangedAfterBackupReject) 'corrupted backup rejected without game write'
    Write-Bytes $backupFile $source[0]

    Write-Bytes (Join-Path $game $script:TargetPaths[0]) ([byte[]](99))
    $rejected = $false
    try { Invoke-Install $game $package $hashes $runner } catch { $rejected = $true }
    Assert-True $rejected 'unknown source hash rejected before install'
    New-Game $game $source[0] $source[1]

    $rolledBack = $false
    try { Invoke-Install $game $package $hashes $runner 1 } catch { $rolledBack = $true }
    $bothSource = ((Get-FileSha256 (Join-Path $game $script:TargetPaths[0])) -eq $hashes[$script:TargetPaths[0]]) -and ((Get-FileSha256 (Join-Path $game $script:TargetPaths[1])) -eq $hashes[$script:TargetPaths[1]])
    Assert-True ($rolledBack -and $bothSource) 'install failure rolls committed file back'

    Invoke-Install $game $package $hashes $runner
    $restoreRolledBack = $false
    try { Invoke-Restore $game $hashes 1 } catch { $restoreRolledBack = $true }
    $packageData = Read-Package $package $hashes
    $bothBuilt = ((Get-TargetState $game $packageData.Records $hashes).Values | Where-Object { $_ -ne 'built' }).Count -eq 0
    Assert-True ($restoreRolledBack -and $bothBuilt) 'restore failure rolls committed file back'

    Write-Host "PASS: $passed assertions; synthetic fixtures only; no game launched"
} finally {
    if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) }
}
