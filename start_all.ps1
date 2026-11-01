# Windows version of start_all.sh: every service in its own PowerShell window, logs in logs\.
# Usage: .\start_all.ps1        (stop everything: .\stop_all.ps1)
# No anvil  -> simulated chain (MOCK_FACILITATOR/MOCK_SIGNER = true).
# No Ollama -> agent is skipped, attacker uses RUN_MODE=direct.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "No virtualenv at $Py" }
New-Item -ItemType Directory -Force logs | Out-Null

& "$PSScriptRoot\stop_all.ps1"   # stop old copies so new code and .env are picked up

function Test-Port($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# --- what this laptop has -------------------------------------------------------------------
$foundry = Join-Path $env:USERPROFILE ".foundry\bin"
if (Test-Path $foundry) { $env:Path = "$foundry;$env:Path" }
$hasAnvil = [bool](Get-Command anvil -ErrorAction SilentlyContinue)
$hasCast = [bool](Get-Command cast -ErrorAction SilentlyContinue)
$hasOllama = $false
try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 | Out-Null; $hasOllama = $true } catch {}

# Env vars set here win over .env (load_dotenv does not override).
# MOCK_SIGNER=true makes callers fake their own signatures and skip the guard, so force it off:
# the real signer (7001) signs offline and needs no chain.
$common = @{ PYTHONUNBUFFERED = "1"; PYTHONIOENCODING = "utf-8"; MOCK_SIGNER = "false" }
if (-not $hasAnvil) { $common.MOCK_FACILITATOR = "true" }  # simulated chain: settlement is faked
$runMode = if ($hasOllama) { "agent" } else { "direct" }

# --- chain (only if anvil exists) -----------------------------------------------------------
if ($hasAnvil) {
    if (-not (Test-Port 8545)) {  # Anvil keeps running between restarts
        Start-Process powershell -ArgumentList "-NoExit", "-Command",
            "`$Host.UI.RawUI.WindowTitle='x402 anvil'; anvil --fork-url https://sepolia.base.org --fork-block-number 52764660 --chain-id 84532 --host 0.0.0.0 --port 8545 --block-time 1 2>&1 | ForEach-Object { `$l = `"`$_`"; Add-Content -Encoding utf8 logs\anvil.log `$l; `$l }"
        for ($i = 0; $i -lt 60 -and -not (Test-Port 8545); $i++) { Start-Sleep 1 }
    }
    if ($hasCast) {
        $rpc = "http://localhost:8545"
        # Sync the fork's clock to the laptop clock (sync_time.sh).
        cast rpc evm_mine --rpc-url $rpc | Out-Null
        $chainTs = [long](cast block latest -f timestamp --rpc-url $rpc)
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        if ([math]::Abs($now - $chainTs) -gt 1) {
            cast rpc evm_setNextBlockTimestamp $now --rpc-url $rpc | Out-Null
            cast rpc evm_mine --rpc-url $rpc | Out-Null
        }
        cast rpc anvil_setIntervalMining 1 --rpc-url $rpc | Out-Null
        # Agent wallet (Anvil #0) has EIP-7702 code on real Base Sepolia: remove it on the fork.
        cast rpc --rpc-url $rpc anvil_setCode 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 0x | Out-Null
        # fund.sh: agent 100 USDC, attacker and lookalike 0 (USDC balances live in slot 9).
        $usdc = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
        $balances = @{
            "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266" = 100
            "0x90F79bf6EB2c4f870365E785982E1f101E93b906" = 0
            "0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC" = 0
        }
        foreach ($addr in $balances.Keys) {
            $slot = cast index address $addr 9
            $value = "0x" + ([long]($balances[$addr] * 1000000)).ToString("x").PadLeft(64, "0")
            cast rpc --rpc-url $rpc anvil_setStorageAt $usdc $slot $value | Out-Null
        }
    } else {
        Write-Warning "anvil found but cast is missing: chain setup (clock, 7702 code, funding) skipped"
    }
}

# --- services, in start order ---------------------------------------------------------------
$services = @(
    @{ name = "signer";      port = 7001; args = "-m signer.server" }
    @{ name = "facilitator"; port = 7002; args = "-m payment.facilitator" }
    @{ name = "defender";    port = 7003; args = "app.py defender" }
    @{ name = "shop";        port = 5001; args = "app.py shop" }
    @{ name = "evil";        port = 5002; args = "app.py evil" }
    @{ name = "newshop";     port = 5003; args = "app.py newshop" }
    @{ name = "agent";       port = 7000; args = "app.py agent"; skip = (-not $hasOllama) }
    @{ name = "attacker";    port = 7004; args = "app.py attacker"; env = @{ RUN_MODE = $runMode } }
    @{ name = "dashboard";   port = 8000; args = "app.py dashboard" }
)

foreach ($s in $services) {
    if ($s.skip) { Write-Host "skip  $($s.name) (Ollama not running)"; continue }
    $vars = $common.Clone()
    if ($s.env) { foreach ($k in $s.env.Keys) { $vars[$k] = $s.env[$k] } }
    $set = ($vars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "
    $cmd = "`$Host.UI.RawUI.WindowTitle='x402 $($s.name) :$($s.port)'; Set-Location '$PSScriptRoot'; $set; " +
           "Set-Content -Encoding utf8 logs\$($s.name).log ''; " +
           "& '$Py' $($s.args) 2>&1 | ForEach-Object { `$l = `"`$_`"; Add-Content -Encoding utf8 logs\$($s.name).log `$l; `$l }"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
}

# --- wait, then report ----------------------------------------------------------------------
$check = @()
if ($hasAnvil) { $check += @{ name = "anvil"; port = 8545 } }
$check += $services | Where-Object { -not $_.skip }
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and ($check | Where-Object { -not (Test-Port $_.port) })) { Start-Sleep 1 }

Write-Host ""
Write-Host ("chain: " + $(if ($hasAnvil) { "anvil (real local fork)" } else { "SIMULATED (no anvil)" }) +
            "   agent: " + $(if ($hasOllama) { "on" } else { "off (no Ollama)" }) + "   attacker RUN_MODE=$runMode")
foreach ($s in $check) {
    if (Test-Port $s.port) { Write-Host "up    $($s.name) :$($s.port)" }
    else { Write-Host "DOWN  $($s.name) :$($s.port) (see logs\$($s.name).log)" -ForegroundColor Red }
}
