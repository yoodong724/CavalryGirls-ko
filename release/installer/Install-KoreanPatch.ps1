[CmdletBinding()]
param(
    [ValidateSet('Install', 'Restore')][string]$Mode = 'Install',
    [string]$GameDir,
    [string]$PackageDirectory,
    [switch]$DetectOnly
)

$ErrorActionPreference = 'Stop'
$script:Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::InputEncoding = $script:Utf8NoBom
[Console]::OutputEncoding = $script:Utf8NoBom
$global:OutputEncoding = $script:Utf8NoBom
$script:GameExe = 'CavalryGirls.exe'
$script:BackupName = '.cavalry-girls-ko-backup'
$script:SourceRevision = 'f1c8fc8f5e5f7470da9ca0ad5a90bca21ab4d75c71e65509b2ffaf75b579eb6a'
$script:TargetPaths = @(
    'CavalryGirls_Data/resources.assets',
    'CavalryGirls_Data/resources.assets.resS'
)
$script:ExpectedSourceHashes = @{
    'CavalryGirls_Data/resources.assets' = 'd275481b891eef752e5d4279c587b564db4c5ef5e0291cc80af82a089d793838'
    'CavalryGirls_Data/resources.assets.resS' = '8d60f1b9828c759f6483fa1998def795c9ed84dd0cd653950e2ee12b2acc6026'
}

function Write-Info([string]$Message) { Write-Host "[한글패치] $Message" }
function Fail([string]$Message) { throw $Message }

function Get-FileSha256([string]$Path) {
    if (-not [IO.File]::Exists($Path)) { Fail "파일이 없습니다: $Path" }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    }
    finally { $stream.Dispose() }
}

function Assert-HexHash([object]$Value, [string]$Name) {
    if (($Value -isnot [string]) -or $Value -notmatch '^[0-9a-f]{64}$') { Fail "$Name 값이 올바른 SHA-256이 아닙니다." }
}

function Test-GameLayout([string]$Path) {
    if (-not [IO.File]::Exists((Join-Path $Path $script:GameExe))) { return $false }
    foreach ($relative in $script:TargetPaths) { if (-not [IO.File]::Exists((Join-Path $Path $relative))) { return $false } }
    return $true
}

function Resolve-GameDirectory([string]$ExplicitGameDir, [string]$PackageRoot) {
    if ($ExplicitGameDir) {
        $resolved = [IO.Path]::GetFullPath($ExplicitGameDir)
        if (-not (Test-GameLayout $resolved)) { Fail "게임 폴더 구조가 올바르지 않습니다: $resolved" }
        return $resolved
    }
    if (-not $PackageRoot) { Fail '패키지 폴더 경로를 확인할 수 없습니다.' }
    $package = [IO.Path]::GetFullPath($PackageRoot)
    $parent = [IO.Directory]::GetParent($package)
    if ($null -eq $parent -or -not (Test-GameLayout $parent.FullName)) {
        Fail "패키지 폴더의 바로 위가 게임 폴더가 아닙니다: $package. 압축을 게임 폴더 안의 하위 폴더 하나로 풀어 주세요."
    }
    return $parent.FullName
}

function Assert-SafeTarget([string]$GameRoot, [string]$Relative) {
    if ($script:TargetPaths -notcontains $Relative) { Fail "허용되지 않은 대상 경로입니다: $Relative" }
    $path = Join-Path $GameRoot $Relative
    if (-not [IO.File]::Exists($path)) { Fail "대상 파일이 없습니다: $Relative" }
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { Fail "재분석 지점 대상은 거부합니다: $Relative" }
    return $path
}

function Get-TargetState([string]$GameRoot, [Collections.IDictionary]$Records, [Collections.IDictionary]$SourceHashes) {
    $states = @{}
    foreach ($relative in $script:TargetPaths) {
        $path = Assert-SafeTarget $GameRoot $relative
        $length = (Get-Item -LiteralPath $path).Length
        $hash = Get-FileSha256 $path
        $record = $Records[$relative]
        if ($hash -eq $SourceHashes[$relative] -and $length -eq [int64]$record.source_size) { $states[$relative] = 'source' }
        elseif ($hash -eq $record.built_sha256 -and $length -eq [int64]$record.built_size) { $states[$relative] = 'built' }
        else { $states[$relative] = 'unknown' }
    }
    return $states
}

function Read-Package([string]$PackageRoot, [Collections.IDictionary]$SourceHashes) {
    $manifestPath = Join-Path $PackageRoot 'package-manifest.json'
    if (-not [IO.File]::Exists($manifestPath)) { Fail 'package-manifest.json이 없습니다.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.source_revision -ne $script:SourceRevision -or $manifest.runtime_verified -ne $false -or $manifest.release_scope -ne 'stage8_local_candidate') { Fail '패키지 버전/배포 범위 정보가 올바르지 않습니다.' }
    if ($manifest.backup_directory -ne $script:BackupName) { Fail '백업 폴더 계약이 일치하지 않습니다.' }
    if (@($manifest.targets).Count -ne 2) { Fail '패키지는 정확히 두 대상 파일을 포함해야 합니다.' }
    $records = @{}
    foreach ($record in @($manifest.targets)) {
        $relative = [string]$record.game_path
        if ($script:TargetPaths -notcontains $relative -or $records.ContainsKey($relative)) { Fail "알 수 없거나 중복된 대상입니다: $relative" }
        foreach ($field in @('source_sha256','built_sha256','delta_sha256')) { Assert-HexHash $record.$field "$relative/$field" }
        if ($record.source_sha256 -ne $SourceHashes[$relative]) { Fail "$relative 원본 hash가 지원 빌드 허용 목록과 다릅니다." }
        if ($record.source_size -isnot [ValueType] -or $record.built_size -isnot [ValueType]) { Fail "$relative 크기 정보가 없습니다." }
        $expectedDelta = ([IO.Path]::GetFileName($relative) + '.xdelta3')
        if ($record.delta -ne $expectedDelta) { Fail "$relative delta 이름이 올바르지 않습니다." }
        $deltaPath = Join-Path $PackageRoot $record.delta
        if (-not [IO.File]::Exists($deltaPath) -or (Get-Item $deltaPath).Length -ne [int64]$record.delta_size -or (Get-FileSha256 $deltaPath) -ne $record.delta_sha256) { Fail "$relative delta hash/크기가 일치하지 않습니다." }
        $records[$relative] = $record
    }
    foreach ($relative in $script:TargetPaths) { if (-not $records.ContainsKey($relative)) { Fail "패키지 대상이 빠졌습니다: $relative" } }
    if (-not $manifest.decoder) { Fail 'decoder 정보가 package manifest에 없습니다.' }
    Assert-HexHash $manifest.decoder.sha256 'decoder.sha256'
    $decoderRelative = [string]$manifest.decoder.path
    if ($decoderRelative -notmatch '^bin[\\/][^\\/]+\.exe$') { Fail 'decoder.path는 패키지 bin 폴더의 exe여야 합니다.' }
    $decoderPath = Join-Path $PackageRoot $decoderRelative
    if (-not [IO.File]::Exists($decoderPath) -or (Get-FileSha256 $decoderPath) -ne $manifest.decoder.sha256) { Fail '번들 decoder hash가 일치하지 않습니다.' }
    return @{ Manifest = $manifest; Records = $records; Decoder = $decoderPath; ManifestHash = Get-FileSha256 $manifestPath }
}

function Copy-Verified([string]$Source, [string]$Destination, [string]$Hash, [int64]$Size) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination)) | Out-Null
    [IO.File]::Copy($Source, $Destination, $true)
    if ((Get-Item -LiteralPath $Destination).Length -ne $Size -or (Get-FileSha256 $Destination) -ne $Hash) { Fail "복사 검증에 실패했습니다: $Destination" }
}

function Replace-FileAtomic([string]$Prepared, [string]$Destination) {
    $sibling = Join-Path ([IO.Path]::GetDirectoryName($Destination)) ('.cgko-replace-' + [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::Copy($Prepared, $sibling, $false)
        $nullString = [System.Management.Automation.Language.NullString]::Value
        [IO.File]::Replace($sibling, $Destination, $nullString, $true)
    } finally { if ([IO.File]::Exists($sibling)) { [IO.File]::Delete($sibling) } }
}

function Invoke-Decoder([string]$Decoder, [string]$Source, [string]$Delta, [string]$Output) {
    try { & $Decoder '-f' '-d' '-s' $Source $Delta $Output }
    catch { Fail "xdelta decoder를 시작하지 못했습니다. Microsoft Visual C++ x64 런타임 설치 여부를 확인하세요: $($_.Exception.Message)" }
    if ($LASTEXITCODE -ne 0) { Fail "xdelta decoder가 실패했습니다 (종료 코드 $LASTEXITCODE)." }
}

function Write-BackupManifest([string]$Path, [object[]]$Targets, [string]$PackageHash) {
    $value = [ordered]@{ schema_version='1.0.0'; source_revision=$script:SourceRevision; package_manifest_sha256=$PackageHash; targets=$Targets }
    [IO.File]::WriteAllText($Path, ($value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

function Read-VerifiedBackup([string]$GameRoot, [Collections.IDictionary]$SourceHashes) {
    $backup = Join-Path $GameRoot $script:BackupName
    $path = Join-Path $backup 'backup-manifest.json'
    if (-not [IO.File]::Exists($path)) { Fail '검증된 백업 manifest가 없습니다.' }
    $manifest = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.source_revision -ne $script:SourceRevision -or @($manifest.targets).Count -ne 2) { Fail '백업 manifest 버전/대상 수가 올바르지 않습니다.' }
    $records = @{}
    foreach ($record in @($manifest.targets)) {
        $relative = [string]$record.game_path
        if ($script:TargetPaths -notcontains $relative -or $records.ContainsKey($relative) -or $record.source_sha256 -ne $SourceHashes[$relative]) { Fail '백업 대상 또는 원본 hash가 올바르지 않습니다.' }
        $expected = ('files/' + $relative).Replace('\','/')
        if ($record.backup_path -ne $expected) { Fail "백업 상대 경로가 올바르지 않습니다: $relative" }
        $file = Join-Path $backup ($record.backup_path.Replace('/','\'))
        if (-not [IO.File]::Exists($file) -or (Get-Item $file).Length -ne [int64]$record.source_size -or (Get-FileSha256 $file) -ne $record.source_sha256) { Fail "백업 검증에 실패했습니다: $relative" }
        $records[$relative] = $record
    }
    return @{ Root=$backup; Records=$records }
}

function Assert-GameNotRunning {
    if (Get-Process -Name 'CavalryGirls' -ErrorAction SilentlyContinue) { Fail 'CavalryGirls가 실행 중입니다. 게임을 정상 종료한 뒤 다시 시도하세요.' }
}

function Invoke-Install([string]$GameRoot, [string]$PackageRoot, [Collections.IDictionary]$SourceHashes = $script:ExpectedSourceHashes, [scriptblock]$DecoderRunner, [int]$FailAfterCommit = 0) {
    Assert-GameNotRunning
    $package = Read-Package $PackageRoot $SourceHashes
    $state = Get-TargetState $GameRoot $package.Records $SourceHashes
    $values = @($state.Values)
    if (($values | Where-Object { $_ -eq 'built' }).Count -eq 2) { Write-Info '이미 같은 한글패치가 설치되어 있습니다.'; return }
    if (($values | Where-Object { $_ -ne 'source' }).Count -ne 0) { Fail '대상 파일 버전이 섞였거나 알 수 없습니다. Steam 파일 검증 후 다시 시도하세요.' }
    $work = Join-Path $GameRoot ('.cgko-stage-' + [guid]::NewGuid().ToString('N'))
    $backup = Join-Path $GameRoot $script:BackupName
    [IO.Directory]::CreateDirectory($work) | Out-Null
    try {
        $built = @{}
        foreach ($relative in $script:TargetPaths) {
            $record = $package.Records[$relative]
            $output = Join-Path $work ([IO.Path]::GetFileName($relative) + '.built')
            if ($DecoderRunner) { & $DecoderRunner $package.Decoder (Join-Path $GameRoot $relative) (Join-Path $PackageRoot $record.delta) $output }
            else { Invoke-Decoder $package.Decoder (Join-Path $GameRoot $relative) (Join-Path $PackageRoot $record.delta) $output }
            if (-not [IO.File]::Exists($output) -or (Get-Item $output).Length -ne [int64]$record.built_size -or (Get-FileSha256 $output) -ne $record.built_sha256) { Fail "복원된 패치 파일 hash/크기가 다릅니다: $relative" }
            $built[$relative] = $output
        }
        if ([IO.Directory]::Exists($backup)) {
            $existingBackup = Read-VerifiedBackup $GameRoot $SourceHashes
            foreach ($relative in $script:TargetPaths) {
                if ($existingBackup.Records[$relative].built_sha256 -ne $package.Records[$relative].built_sha256 -or [int64]$existingBackup.Records[$relative].built_size -ne [int64]$package.Records[$relative].built_size) {
                    Fail '기존 백업은 다른 패치 빌드용입니다. 백업을 보존한 채 설치를 중단합니다.'
                }
            }
        }
        else {
            $backupStage = Join-Path $GameRoot ('.cgko-backup-stage-' + [guid]::NewGuid().ToString('N'))
            [IO.Directory]::CreateDirectory((Join-Path $backupStage 'files')) | Out-Null
            try {
                $backupRows = @()
                foreach ($relative in $script:TargetPaths) {
                    $record = $package.Records[$relative]
                    $backupFile = Join-Path (Join-Path $backupStage 'files') $relative
                    Copy-Verified (Join-Path $GameRoot $relative) $backupFile $record.source_sha256 ([int64]$record.source_size)
                    $backupRows += [ordered]@{ game_path=$relative; source_sha256=$record.source_sha256; source_size=[int64]$record.source_size; built_sha256=$record.built_sha256; built_size=[int64]$record.built_size; backup_path=('files/' + $relative).Replace('\','/') }
                }
                Write-BackupManifest (Join-Path $backupStage 'backup-manifest.json') $backupRows $package.ManifestHash
                [IO.Directory]::Move($backupStage, $backup)
            } finally { if ([IO.Directory]::Exists($backupStage)) { [IO.Directory]::Delete($backupStage, $true) } }
        }
        $changed = New-Object Collections.Generic.List[string]
        try {
            foreach ($relative in $script:TargetPaths) {
                Replace-FileAtomic $built[$relative] (Join-Path $GameRoot $relative)
                $changed.Add($relative)
                if ($FailAfterCommit -gt 0 -and $changed.Count -eq $FailAfterCommit) { Fail 'fixture rollback injection' }
            }
            $after = Get-TargetState $GameRoot $package.Records $SourceHashes
            if ((@($after.Values) | Where-Object { $_ -ne 'built' }).Count -ne 0) { Fail '설치 후 검증에 실패했습니다.' }
        } catch {
            $original = $_
            $rollbackErrors = New-Object Collections.Generic.List[string]
            $changedPaths = @($changed.ToArray())
            [array]::Reverse($changedPaths)
            foreach ($relative in $changedPaths) {
                try {
                    $backupFile = Join-Path $backup ('files/' + $relative).Replace('/','\')
                    Replace-FileAtomic $backupFile (Join-Path $GameRoot $relative)
                } catch { $rollbackErrors.Add("${relative}: $($_.Exception.Message)") }
            }
            try {
                $rollbackState = Get-TargetState $GameRoot $package.Records $SourceHashes
                if ((@($rollbackState.Values) | Where-Object { $_ -ne 'source' }).Count -ne 0) { $rollbackErrors.Add('rollback 후 두 대상의 원본 hash/크기가 일치하지 않습니다.') }
            } catch { $rollbackErrors.Add("rollback 사후 검증 실패: $($_.Exception.Message)") }
            if ($rollbackErrors.Count) { Fail "설치 실패 후 일부 롤백도 실패했습니다: $($rollbackErrors -join '; ')" }
            throw $original
        }
        Write-Info '두 파일 설치와 사후 검증을 완료했습니다. 게임 실행 검증은 사용자가 직접 수행해야 합니다.'
    } finally { if ([IO.Directory]::Exists($work)) { [IO.Directory]::Delete($work, $true) } }
}

function Invoke-Restore([string]$GameRoot, [Collections.IDictionary]$SourceHashes = $script:ExpectedSourceHashes, [int]$FailAfterCommit = 0) {
    Assert-GameNotRunning
    $backup = Read-VerifiedBackup $GameRoot $SourceHashes
    $records = $backup.Records
    $state = Get-TargetState $GameRoot $records $SourceHashes
    if ((@($state.Values) | Where-Object { $_ -eq 'source' }).Count -eq 2) { Write-Info '이미 원본 상태입니다. 검증된 백업은 보존합니다.'; return }
    if ((@($state.Values) | Where-Object { $_ -ne 'built' }).Count -ne 0) { Fail '대상 파일이 설치 상태와 다르거나 섞여 있어 복원을 중단합니다.' }
    $work = Join-Path $GameRoot ('.cgko-restore-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($work) | Out-Null
    try {
        $rollback = @{}
        foreach ($relative in $script:TargetPaths) {
            $record = $records[$relative]
            $copy = Join-Path $work ([IO.Path]::GetFileName($relative) + '.current')
            Copy-Verified (Join-Path $GameRoot $relative) $copy $record.built_sha256 ([int64]$record.built_size)
            $rollback[$relative] = $copy
        }
        $changed = New-Object Collections.Generic.List[string]
        try {
            foreach ($relative in $script:TargetPaths) {
                Replace-FileAtomic (Join-Path $backup.Root ('files/' + $relative).Replace('/','\')) (Join-Path $GameRoot $relative)
                $changed.Add($relative)
                if ($FailAfterCommit -gt 0 -and $changed.Count -eq $FailAfterCommit) { Fail 'fixture rollback injection' }
            }
            $after = Get-TargetState $GameRoot $records $SourceHashes
            if ((@($after.Values) | Where-Object { $_ -ne 'source' }).Count -ne 0) { Fail '복원 후 검증에 실패했습니다.' }
        } catch {
            $original = $_
            $rollbackErrors = New-Object Collections.Generic.List[string]
            $changedPaths = @($changed.ToArray())
            [array]::Reverse($changedPaths)
            foreach ($relative in $changedPaths) {
                try { Replace-FileAtomic $rollback[$relative] (Join-Path $GameRoot $relative) }
                catch { $rollbackErrors.Add("${relative}: $($_.Exception.Message)") }
            }
            try {
                $rollbackState = Get-TargetState $GameRoot $records $SourceHashes
                if ((@($rollbackState.Values) | Where-Object { $_ -ne 'built' }).Count -ne 0) { $rollbackErrors.Add('rollback 후 두 대상의 설치 hash/크기가 일치하지 않습니다.') }
            } catch { $rollbackErrors.Add("rollback 사후 검증 실패: $($_.Exception.Message)") }
            if ($rollbackErrors.Count) { Fail "복원 실패 후 일부 롤백도 실패했습니다: $($rollbackErrors -join '; ')" }
            throw $original
        }
        Write-Info '원본 두 파일 복원을 완료했습니다. 검증된 백업은 보존했습니다.'
    } finally { if ([IO.Directory]::Exists($work)) { [IO.Directory]::Delete($work, $true) } }
}

function Main {
    try {
        $packageRoot = if ($PackageDirectory) { [IO.Path]::GetFullPath($PackageDirectory) } else { Split-Path -Parent $PSCommandPath }
        $root = Resolve-GameDirectory $GameDir $packageRoot
        Write-Info "게임 폴더: $root"
        if ($DetectOnly) { Write-Info '감지만 수행했습니다. 파일을 변경하거나 게임을 실행하지 않았습니다.'; return 0 }
        if ($Mode -eq 'Install') { Invoke-Install $root $packageRoot }
        else { Invoke-Restore $root }
        return 0
    } catch {
        Write-Error "실패: $($_.Exception.Message) 쓰기 권한 문제라면 Steam과 게임을 종료하고 쓰기 가능한 계정에서 다시 실행하세요."
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') { exit (Main) }
