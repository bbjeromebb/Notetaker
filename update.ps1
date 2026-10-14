<#
  Notetaker bijwerken naar de nieuwste versie op GitHub.

  Gebruik, in PowerShell vanuit de projectmap:
      powershell -ExecutionPolicy Bypass -File .\update.ps1

  Wordt ook gestart via "Bijwerken" in het menu van het systeemvak (met -Restart).
  Configuratie, vault, modellen en snelkoppelingen blijven ongewijzigd.
#>
param(
    [switch]$Restart
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$VPy = Join-Path $Root ".venv\Scripts\python.exe"
$NtApp = Join-Path $Root ".venv\Scripts\notetaker-app.exe"
$Log = Join-Path $env:APPDATA "Notetaker\update.log"
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null

function Say([string]$text, [string]$color = "Gray") {
    Write-Host $text -ForegroundColor $color
    Add-Content -Path $Log -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $text)
}
function Start-App {
    if (Test-Path $NtApp) { Start-Process $NtApp -WorkingDirectory $Root }
}
function Fail([string]$text) {
    Say "FOUT: $text" "Red"
    # Altijd opnieuw starten, ook na een mislukte update: de vorige versie blijft bruikbaar
    if ($Restart) { Start-App }
    exit 1
}

if (-not (Test-Path (Join-Path $Root ".git"))) { Fail "Geen Git-map: installeer via git clone (zie README)." }
if (-not (Test-Path $VPy)) { Fail "Geen installatie gevonden: voer eerst install.ps1 uit." }

Say "Notetaker bijwerken..." "Cyan"
$before = (& git -C $Root rev-parse HEAD).Trim()
$pyprojectBefore = (Get-FileHash (Join-Path $Root "pyproject.toml")).Hash

& git -C $Root pull --ff-only
if ($LASTEXITCODE -ne 0) { Fail "git pull mislukt. Lokale wijzigingen in de projectmap? Zie: git -C `"$Root`" status" }
$after = (& git -C $Root rev-parse HEAD).Trim()

if ($before -eq $after) {
    Say "Al de nieuwste versie." "Green"
    if ($Restart) { Start-App }
    exit 0
}
& git -C $Root log --format="  - %s" "$before..$after" | ForEach-Object { Say $_ }

# De app stoppen: pip kan notetaker-app.exe niet vervangen zolang die draait
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "notetaker-app" -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# Pakketten enkel opnieuw installeren als de afhankelijkheden veranderden; anders volstaat de pull
$pyprojectAfter = (Get-FileHash (Join-Path $Root "pyproject.toml")).Hash
if ($pyprojectBefore -ne $pyprojectAfter) {
    Say "Afhankelijkheden gewijzigd, pakketten bijwerken..."
    $extras = ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $extras = "[gpu]" }
    Push-Location $Root
    & $VPy -m pip install --quiet -e ".$extras"
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "pip install mislukt (code $code)." }
}

Say "Bijgewerkt naar $($after.Substring(0, 7))." "Green"
if ($Restart) {
    Start-App
    Say "Notetaker opnieuw gestart."
} else {
    Say "Start Notetaker opnieuw via het startmenu, of voer dit script uit met -Restart."
}
