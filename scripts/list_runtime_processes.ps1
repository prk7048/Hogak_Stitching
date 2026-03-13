$targets = @("stitch_runtime.exe", "ffmpeg.exe", "ffplay.exe", "vlc.exe", "python.exe")

$processes = Get-CimInstance Win32_Process |
    Where-Object { $targets -contains $_.Name } |
    Where-Object {
        $_.CommandLine -match "23000" -or
        $_.CommandLine -match "24000" -or
        $_.CommandLine -match "stitching\.cli" -or
        $_.CommandLine -match "stitch_runtime\.exe"
    } |
    Sort-Object Name, ProcessId

foreach ($process in $processes) {
    Write-Output ("[{0}] {1}" -f $process.ProcessId, $process.Name)
    Write-Output $process.CommandLine
    Write-Output ""
}
