<#
  Notetaker installeren op Windows 10/11.

  Gebruik, in PowerShell vanuit de projectmap:
      powershell -ExecutionPolicy Bypass -File .\install.ps1

  Opties:
      -NoGpu            Whisper niet op de NVIDIA-kaart installeren
      -NoAutostart      Notetaker niet automatisch starten bij aanmelden
      -SummaryModel     taalmodel voor verslagen   (standaard gemma3:12b)
      -CleanupModel     taalmodel voor dictee      (standaard gemma3:4b)

  Het script is herhaalbaar: opnieuw uitvoeren werkt een bestaande installatie bij.
#>
param(
    [switch]$NoGpu,
    [switch]$NoAutostart,
    [string]$SummaryModel = "gemma3:12b",
    [string]$CleanupModel = "gemma3:4b"
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VPy = Join-Path $Venv "Scripts\python.exe"
$Nt = Join-Path $Venv "Scripts\notetaker.exe"
$NtApp = Join-Path $Venv "Scripts\notetaker-app.exe"

function Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "    $text" -ForegroundColor Green }
function Warn([string]$text) { Write-Host "    $text" -ForegroundColor Yellow }
function Fail([string]$text) {
    Write-Host "`nFOUT: $text" -ForegroundColor Red
    Write-Host "Los dit op en voer het script opnieuw uit." -ForegroundColor Red
    exit 1
}
function Has([string]$cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Run([string]$exe, [string[]]$arguments, [string]$what) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what mislukt (code $LASTEXITCODE)." }
}
function Winget-Install([string]$id, [string]$name) {
    if (-not (Has "winget")) {
        Fail "winget ontbreekt. Installeer 'App-installatieprogramma' uit de Microsoft Store."
    }
    Write-Host "    $name installeren via winget..."
    winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
    # -1978335189: al geinstalleerd, geen upgrade beschikbaar
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        Fail "Installatie van $name mislukt (code $LASTEXITCODE)."
    }
    Refresh-Path
}
function Ollama-Up {
    try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    Fail "Start dit script vanuit de Notetaker-map (pyproject.toml niet gevonden in $Root)."
}

# 1. Python ---------------------------------------------------------------------
Step "Python 3.12"
$hasPy = $false
if (Has "py") {
    & py -3.12 -c "import sys" 2>$null
    $hasPy = ($LASTEXITCODE -eq 0)
}
if (-not $hasPy) { Winget-Install "Python.Python.3.12" "Python 3.12" }
if (-not (Has "py")) { Fail "Python-launcher 'py' niet gevonden. Sluit PowerShell, open opnieuw en herstart het script." }
Ok (& py -3.12 --version)

# 2. Virtuele omgeving en pakketten -----------------------------------------------
Step "Notetaker en afhankelijkheden"
if (-not (Test-Path $VPy)) { Run "py" @("-3.12", "-m", "venv", $Venv) "Virtuele omgeving maken" }
Run $VPy @("-m", "pip", "install", "--upgrade", "--quiet", "pip") "pip bijwerken"

$gpu = (-not $NoGpu) -and (Has "nvidia-smi")
$extras = ""
if ($gpu) {
    $card = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null) -join ", "
    Ok "NVIDIA-GPU gevonden: $card"
    $extras = "[gpu]"
} elseif ($NoGpu) {
    Warn "GPU overgeslagen (-NoGpu): Whisper draait op de processor."
} else {
    Warn "Geen NVIDIA-driver gevonden (nvidia-smi ontbreekt): Whisper draait op de processor."
}
Push-Location $Root
Run $VPy @("-m", "pip", "install", "--quiet", "-e", ".$extras") "Pakketten installeren"
Pop-Location
Ok "Pakketten geinstalleerd"

# 3. Ollama en taalmodellen ---------------------------------------------------------
Step "Ollama en taalmodellen"
if (-not (Has "ollama")) { Winget-Install "Ollama.Ollama" "Ollama" }
if (-not (Has "ollama")) { Fail "Ollama niet gevonden na installatie. Open PowerShell opnieuw en herstart het script." }
if (-not (Ollama-Up)) {
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Ollama-Up); $i++) { Start-Sleep -Seconds 1 }
}
if (-not (Ollama-Up)) { Fail "Ollama start niet op http://localhost:11434." }
foreach ($model in (@($CleanupModel, $SummaryModel) | Select-Object -Unique)) {
    Write-Host "    $model ophalen (kan enkele minuten duren)..."
    Run "ollama" @("pull", $model) "Model $model ophalen"
}
Ok "Taalmodellen klaar"

# 4. Configuratie ---------------------------------------------------------------------
Step "Configuratie"
Run $Nt @("init-config") "Configuratie aanmaken"
Run $Nt @("set", "llm.cleanup_model", $CleanupModel) "Instelling"
Run $Nt @("set", "llm.summary_model", $SummaryModel) "Instelling"
if ($gpu) {
    # Kleine kaart: videogeheugen voorbehouden aan Whisper
    Run $Nt @("set", "llm.num_gpu", "0") "Instelling"
}
Run $Nt @("set", "obsidian.audio_in_vault", "true") "Instelling"

$current = (& $VPy -c "from notetaker.config import load; print(load().obsidian.vault)").Trim()
if ($current) {
    Ok "Obsidian-vault: $current"
    $vault = ""
} else {
    Write-Host ""
    & $Nt vault
    $vault = Read-Host "    Pad naar je Obsidian-vault (Enter = later kiezen via het menu)"
}
if ($vault) {
    $vault = $vault.Trim('"', ' ')
    & $Nt vault $vault
    if ($LASTEXITCODE -ne 0) { Warn "Vault niet ingesteld; kies hem later via het menu in het systeemvak." }
}

# 5. Whisper downloaden en GPU testen --------------------------------------------------
Step "Spraakmodel downloaden en testen"
Run $Nt @("check", "--load") "Controle"

# 6. Snelkoppelingen -------------------------------------------------------------------
Step "Snelkoppelingen"
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut([string]$path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $NtApp
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Notetaker: dicteren en vergaderingen"
    $lnk.Save()
}
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Shortcut (Join-Path $programs "Notetaker.lnk")
Ok "Startmenu: Notetaker"
if (-not $NoAutostart) {
    New-Shortcut (Join-Path $programs "Startup\Notetaker.lnk")
    Ok "Start automatisch bij aanmelden (uitzetten: snelkoppeling in shell:startup verwijderen)"
}

# 7. Starten ---------------------------------------------------------------------------
Step "Notetaker starten"
# Een eerdere versie stoppen (de launcher start pythonw als kindproces)
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "notetaker-app" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process $NtApp -WorkingDirectory $Root
Ok "Het microfoonicoon verschijnt rechtsonder in het systeemvak (eventueel onder het pijltje)."
Write-Host ""
Write-Host "Klaar. Houd Ctrl+Win ingedrukt om te dicteren." -ForegroundColor Green
Write-Host "Logbestand bij problemen: $env:APPDATA\Notetaker\notetaker.log"
