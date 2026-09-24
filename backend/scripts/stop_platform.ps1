param([Parameter(Mandatory=$true)][string]$Workspace)

# 仅终止本仓虚拟环境启动的四个服务及其子进程；不按全局 python 名称杀进程。
$workspacePath = (Resolve-Path -LiteralPath $Workspace -ErrorAction Stop).Path
$launcherRoot = [regex]::Escape((Join-Path $workspacePath '.venv\Scripts\'))
$launcherPattern = $launcherRoot + 'liveprofit-(?:api|worker|dispatcher|market-worker)(?:\.exe)?(?:["\s]|$)'
$processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
$roots = @($processes | Where-Object { $_.CommandLine -and $_.CommandLine -match $launcherPattern })
foreach ($serviceProcess in $roots) {
    # /T 包含 worker 启动的采集子进程；只接受上面查到的本仓服务 PID。
    & taskkill.exe /PID $serviceProcess.ProcessId /T /F 2>$null | Out-Null
}
