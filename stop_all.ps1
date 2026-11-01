# Stops the demo services by port (not by process name). Anvil keeps running unless -Chain is given.
# Usage: .\stop_all.ps1   |   .\stop_all.ps1 -Chain
param([switch]$Chain)
$ports = @(7000, 7001, 7002, 7003, 7004, 5001, 5002, 5003, 8000)
if ($Chain) { $ports += 8545 }

foreach ($port in $ports) {
    $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        if ($procId -le 4) { continue }  # never touch System/Idle
        # Find the window start_all.ps1 opened for it BEFORE killing anything: the venv python.exe is a
        # launcher whose child holds the port, and it exits as soon as the child dies.
        $kill = @($procId)
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 3 -and $p; $i++) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
            if (-not $p) { break }
            if ($p.Name -eq "python.exe") { $kill += $p.ProcessId; continue }
            if ($p.Name -eq "powershell.exe" -and $p.CommandLine -like "*WindowTitle='x402 *") { $kill += $p.ProcessId }
            break
        }
        foreach ($k in $kill) { Stop-Process -Id $k -Force -ErrorAction SilentlyContinue }
        Write-Host "stopped :$port (pid $procId)"
    }
}
