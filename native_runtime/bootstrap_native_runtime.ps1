param(
    [string]$Preset = "windows-release"
)

$ErrorActionPreference = "Stop"

function Resolve-CommandPath {
    param(
        [string]$CommandName
    )

    try {
        $command = Get-Command -Name $CommandName -ErrorAction Stop
        return $command.Source
    } catch {
        return ""
    }
}

function Invoke-VersionProbe {
    param(
        [string[]]$Command
    )

    try {
        return (& $Command[0] $Command[1..($Command.Length - 1)] 2>$null | Select-Object -First 1)
    } catch {
        return ""
    }
}

function Resolve-FirstPath {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $PSScriptRoot "toolchain_versions.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

$opencvResolved = Resolve-FirstPath @(
    $env:OpenCV_DIR,
    $env:OPENCV_DIR,
    (Join-Path $repoRoot ".third_party\opencv-python\_skbuild\win-amd64-3.12\cmake-install\x64\vc17\staticlib")
)
$ffmpegResolved = Resolve-FirstPath @(
    $env:HOGAK_FFMPEG_DEV_ROOT,
    $env:FFMPEG_DEV_ROOT,
    (Join-Path $repoRoot ".third_party\ffmpeg-dev\current"),
    (Join-Path $repoRoot ".third_party\ffmpeg\current")
)
$cudaResolved = Resolve-FirstPath @(
    $env:CUDAToolkit_ROOT,
    $env:CUDA_PATH,
    $env:CUDA_PATH_V13_1
)

$summary = [ordered]@{
    preset = $Preset
    repo_root = $repoRoot
    manifest = $manifestPath
    opencv_dir = $opencvResolved
    ffmpeg_dev_root = $ffmpegResolved
    cuda_root = $cudaResolved
    cmake = Resolve-CommandPath "cmake"
    python = Resolve-CommandPath "python"
    node = Resolve-CommandPath "node"
    npm = Resolve-CommandPath "npm"
    frontend_dir = Join-Path $repoRoot "frontend"
}

$missing = @()
if ([string]::IsNullOrWhiteSpace($opencvResolved)) {
    $missing += "OpenCV_DIR"
}
if ([string]::IsNullOrWhiteSpace($ffmpegResolved)) {
    $missing += "HOGAK_FFMPEG_DEV_ROOT"
}
if ([string]::IsNullOrWhiteSpace($cudaResolved)) {
    $missing += "CUDAToolkit_ROOT"
}
if ([string]::IsNullOrWhiteSpace($summary.python)) {
    $missing += "python"
}
if ([string]::IsNullOrWhiteSpace($summary.node)) {
    $missing += "node"
}
if ([string]::IsNullOrWhiteSpace($summary.npm)) {
    $missing += "npm"
}

$pythonVersion = ""
$nodeVersion = ""
$npmVersion = ""
$backendImportCheck = ""

if (-not [string]::IsNullOrWhiteSpace($summary.python)) {
    $pythonVersion = Invoke-VersionProbe @("python", "--version")
    try {
        $backendImportCheck = (& python -c "import fastapi, uvicorn, stitching.runtime_backend; print('ok')" 2>$null | Select-Object -First 1)
    } catch {
        $backendImportCheck = ""
    }
}
if (-not [string]::IsNullOrWhiteSpace($summary.node)) {
    $nodeVersion = Invoke-VersionProbe @("node", "--version")
}
if (-not [string]::IsNullOrWhiteSpace($summary.npm)) {
    $npmVersion = Invoke-VersionProbe @("npm", "--version")
}

$summary.python_version = $pythonVersion
$summary.node_version = $nodeVersion
$summary.npm_version = $npmVersion
$summary.backend_import_check = $backendImportCheck

if (-not (Test-Path -LiteralPath $summary.frontend_dir)) {
    $missing += "frontend"
}
if ([string]::IsNullOrWhiteSpace($backendImportCheck) -or $backendImportCheck -ne "ok") {
    $missing += "python-backend-import"
}

$summaryJson = $summary | ConvertTo-Json -Depth 4
Write-Output $summaryJson

if ($missing.Count -gt 0) {
    throw "Missing native runtime prerequisites: $($missing -join ', ')"
}

$presetPath = Join-Path $PSScriptRoot "CMakePresets.json"
if (-not (Test-Path -LiteralPath $presetPath)) {
    throw "CMakePresets.json not found at $presetPath"
}

Write-Output "Bootstrap check passed. Configure with: cmake --preset $Preset"
