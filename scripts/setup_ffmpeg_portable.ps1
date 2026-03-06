param(
    [string]$DownloadUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    [string]$InstallRoot = ".third_party/ffmpeg"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$installRootPath = Join-Path $projectRoot $InstallRoot
$downloadDir = Join-Path $installRootPath "downloads"
$extractDir = Join-Path $installRootPath "extracted"
$currentDir = Join-Path $installRootPath "current"
$zipPath = Join-Path $downloadDir "ffmpeg-release-essentials.zip"

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

Write-Host "Downloading FFmpeg portable build..."
Invoke-WebRequest -Uri $DownloadUrl -OutFile $zipPath

if (Test-Path $currentDir) {
    Remove-Item -Recurse -Force $currentDir
}

Write-Host "Extracting archive..."
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

$root = Get-ChildItem -Path $extractDir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $root) {
    throw "Could not locate extracted FFmpeg directory."
}

Move-Item -Path $root.FullName -Destination $currentDir

$ffmpegBin = Join-Path $currentDir "bin\ffmpeg.exe"
$ffprobeBin = Join-Path $currentDir "bin\ffprobe.exe"

if (-not (Test-Path $ffmpegBin)) {
    throw "ffmpeg.exe not found after extraction."
}
if (-not (Test-Path $ffprobeBin)) {
    throw "ffprobe.exe not found after extraction."
}

Write-Host "Installed:"
Write-Host "  FFmpeg : $ffmpegBin"
Write-Host "  FFprobe: $ffprobeBin"
