param(
    [string]$PythonExe = "py",
    [string]$OpencvPythonRef = "92",
    [string]$CmakeGenerator = "Visual Studio 17 2022",
    [string]$GitExe = "",
    [string]$WithCudnn = "OFF",
    [string]$WithDnnCuda = "OFF"
)

$ErrorActionPreference = "Stop"

function Test-Cmd($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    return $null -ne $cmd
}

Write-Host "== CUDA OpenCV setup (Windows) =="

if ([string]::IsNullOrWhiteSpace($GitExe)) {
    if (Test-Cmd "git") {
        $GitExe = "git"
    } elseif (Test-Path "C:\Program Files\Git\cmd\git.exe") {
        $GitExe = "C:\Program Files\Git\cmd\git.exe"
    } elseif (Test-Path "C:\Program Files\Git\bin\git.exe") {
        $GitExe = "C:\Program Files\Git\bin\git.exe"
    }
}

if ([string]::IsNullOrWhiteSpace($GitExe)) {
    throw "git not found in PATH and default install paths"
}

if (-not (Test-Cmd "nvcc")) {
    throw "nvcc not found. Install CUDA Toolkit and reopen terminal."
}

if (-not (Test-Cmd "cl")) {
    throw "MSVC cl.exe not found. Run in 'x64 Native Tools Command Prompt for VS' or install Build Tools."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

& $PythonExe -m pip install -U pip setuptools wheel
# opencv-python 4.6 build scripts still rely on numpy.distutils.
& $PythonExe -m pip install -U "numpy<2" scikit-build ninja cmake
& $PythonExe -m pip install -U "scikit-build-core>=0.10" "packaging>=24" "pybind11>=2.12"

$root = Join-Path $projectRoot ".third_party"
$repo = Join-Path $root "opencv-python"
New-Item -ItemType Directory -Force -Path $root | Out-Null

if (-not (Test-Path $repo)) {
    & $GitExe clone https://github.com/opencv/opencv-python.git $repo
}

Push-Location $repo
try {
    & $GitExe fetch --all --tags
    & $GitExe checkout $OpencvPythonRef

    $env:ENABLE_CONTRIB = "1"
    $env:ENABLE_HEADLESS = "0"
    # Ninja generator does not accept "-A x64" in this build path.
    # Force Visual Studio generator on Windows to avoid "platform x64 was specified" error.
    $env:CMAKE_GENERATOR = $CmakeGenerator
    $env:CMAKE_ARGS = @(
        "-DWITH_CUDA=ON",
        "-DWITH_CUDNN=$WithCudnn",
        "-DOPENCV_DNN_CUDA=$WithDnnCuda",
        "-DCUDA_FAST_MATH=ON",
        "-DWITH_CUBLAS=ON"
    ) -join " "

    & $PythonExe -m pip wheel . -w dist --verbose --no-deps --no-build-isolation
    $wheel = Get-ChildItem dist -Filter "*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $wheel) {
        throw "wheel build failed: no wheel produced"
    }
    & $PythonExe -m pip install --force-reinstall $wheel.FullName

    Write-Host "Installed wheel: $($wheel.FullName)"
    @'
import cv2
print("cv2", cv2.__version__)
print("has_cuda", hasattr(cv2, "cuda"))
print("cuda_devices", cv2.cuda.getCudaEnabledDeviceCount() if hasattr(cv2, "cuda") else -1)
'@ | & $PythonExe -
}
finally {
    Pop-Location
}
