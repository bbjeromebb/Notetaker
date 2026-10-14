# Notetaker

Dicteren in elke Windows-app en vergaderingen (ook Teams, Zoom of Meet) transcriberen en samenvatten. Functionele herbouw van Wispr Flow in Python, aangevuld met vergaderverslagen. Analyse en architectuur: [docs/analyse-wispr-flow.md](docs/analyse-wispr-flow.md).

## Installatie (Windows)

Open PowerShell (Windows-toets, typ `powershell`, Enter) en voer uit:

```powershell
winget install -e --id Git.Git
# PowerShell sluiten en opnieuw openen, zodat git gekend is
git clone -b claude/wispr-flow-reverse-engineer-yvswhb https://github.com/bbjeromebb/notetaker.git $HOME\Notetaker
cd $HOME\Notetaker
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Bij `git clone` vraagt Windows om in te loggen bij GitHub, omdat de repository privé is. Zonder Git: download de branch als ZIP via GitHub, pak uit en start `install.ps1` vanuit die map.

Het script installeert Python 3.12 en Ollama via winget, zet een virtuele omgeving op, installeert de GPU-bibliotheken als er een NVIDIA-kaart is, haalt `gemma3:4b` en `gemma3:12b` op, maakt de configuratie aan, vraagt naar je Obsidian-vault, downloadt het spraakmodel en test de GPU. Daarna maakt het een snelkoppeling in het startmenu en bij het aanmelden, en start het de app. Reken op 15 tot 30 minuten, vooral door ongeveer 11 GB aan modellen.

Bijwerken: `git pull` en het script opnieuw uitvoeren. Opties: `-NoGpu`, `-NoAutostart`, `-SummaryModel gemma3:4b`.

Handmatig installeren:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[gpu]"
notetaker init-config
ollama pull gemma3:4b
ollama pull gemma3:12b
```

### NVIDIA-GPU (bv. GeForce MX550)

```powershell
pip install -e ".[gpu]"   # cuBLAS (CUDA 12) en cuDNN 9 als pip-pakketten
notetaker check           # toont of Whisper op de GPU of de CPU draait
```

De app zoekt de NVIDIA-DLL's zelf op en valt terug op de CPU als ze ontbreken of als het videogeheugen tekortschiet, in plaats van te crashen. Whisper draait op de GPU in `int8_float16`, wat in 2 GB past. Werkt de pip-route niet, dan bevat het archief van [whisper-standalone-win](https://github.com/Purfview/whisper-standalone-win/releases) dezelfde DLL's; zet die in een map die in `PATH` staat.

Bij 2 GB videogeheugen passen Whisper en het taalmodel niet samen op de kaart. Zet daarom in de configuratie `[llm] num_gpu = 0`: Ollama rekent dan op de CPU en Whisper houdt de GPU. Met minder dan 16 GB werkgeheugen: `summary_model = "gemma3:4b"`.

## Gebruik

| Actie | Toetsen |
|---|---|
| Dicteren | Ctrl+Win vasthouden, praten, loslaten |
| Hands-free | Dubbeltik Ctrl+Win (of Spatie tijdens vasthouden); opnieuw Ctrl+Win om te stoppen |
| Selectie herschrijven | Tekst selecteren, Ctrl+Win+Alt vasthouden, opdracht inspreken ("maak dit formeler") |
| Annuleren | Esc |

Tijdens het dicteren verschijnt onderaan het scherm een klein opnamevenster met de toestand (luisteren, hands-free, opdracht, verwerken) en het microfoonniveau. Het venster neemt nooit de focus over en laat muisklikken door, zodat de tekst in de juiste app terechtkomt. Uitzetten: `[dictation] overlay = false`.

```powershell
notetaker run                         # systeemvak-app: dicteren + vergaderknop
notetaker meeting --title "Stuurgroep" # opname tot Ctrl+C, verslag in Documenten\Notetaker
notetaker transcribe opname.m4a --summarize
notetaker summarize verslag.md
notetaker vault "C:\Users\naam\Documents\MijnVault"   # verslagen in Obsidian
notetaker vault                        # huidige en bekende vaults tonen
notetaker vault --off                  # terug naar Documenten\Notetaker
notetaker check [--load]               # GPU, Ollama-modellen en opslagmappen controleren
notetaker set llm.num_gpu 0            # één instelling wijzigen
notetaker devices                      # audioapparaten tonen
```

Een vergadering levert één Markdown-bestand op met samenvatting, besproken punten, beslissingen, actiepunten, open vragen en de volledige transcriptie met tijdstempels, plus de opname als `.ogg` (Opus; `audio_format = "wav"` voor WAV). Tijdens de opname groeit de transcriptie live in hetzelfde bestand.

## Obsidian

Kies de vault via het systeemvak (Verslagen opslaan in > vault) of met `notetaker vault`. Het menu toont de vaults die Obsidian zelf kent; "Andere map kiezen..." opent een mapkiezer. Verslagen komen dan in `<vault>/Vergaderingen` met:

- YAML-frontmatter (`title`, `date`, `time`, `duration`, `type`, `tags`, `audio`), bruikbaar in Dataview en Bases
- actiepunten als taken (`- [ ] Wat (wie, deadline)`), bruikbaar in de Tasks-plugin
- de opname als Opus (`.ogg`, ongeveer 15 MB per uur), standaard buiten de vault in `Documenten\Notetaker\audio`. Met `audio_in_vault = true` komt de opname naast de notitie en speelt ze af in Obsidian via `![[...]]`.

Tijdens de opname staat bovenaan de notitie een sectie **Mijn notities**. Wat je daar in Obsidian typt, blijft bewaard bij elke tussentijdse update van de transcriptie en gaat mee naar het taalmodel voor het verslag. Eigen tussenkoppen kunnen, zolang ze niet "Verslag", "Samenvatting" of "Transcriptie" heten.

Notities worden geschreven via een tijdelijk bestand en in één stap hernoemd; opnames worden eerst volledig buiten de vault gecodeerd en daarna verplaatst. Syncthing en Obsidian zien zo nooit een half geschreven bestand.

Map, tags en taakformaat staan onder `[obsidian]` in de configuratie.

## Configuratie

`%APPDATA%\Notetaker\config.toml`, zie [config.example.toml](src/notetaker/config.example.toml): woordenboek, vaste correcties, snippets, stijl per app, sneltoetsen, modellen.

| Keuze | Privacy | Snelheid | Kost |
|---|---|---|---|
| `stt.backend = "faster-whisper"` (standaard) | audio blijft lokaal | CPU traag, GPU snel | gratis |
| `llm.backend = "ollama"` (standaard) | tekst blijft lokaal | 1 tot 5 s per dictee, afhankelijk van hardware | gratis |
| `llm.backend = "openai-compatible"` + Groq | tekst naar de VS | snel | gratis tier met rate limits |
| `llm.backend = "openai-compatible"` + Gemini | Google mag gratis invoer gebruiken | snel | gratis tier |
| `llm.backend = "anthropic"` | tekst naar Anthropic | snel | betalend (`pip install -e ".[claude]"`) |
| `llm.backend = "none"` | volledig lokaal | direct | gratis, enkel basisopkuis |

Voor Groq of Gemini: `pip install -e ".[api]"`, maak een gratis sleutel aan en zet die als omgevingsvariabele (`setx GROQ_API_KEY "..."`). Voorbeeldinstellingen staan in commentaar in de configuratie.

Lange vergaderingen worden in delen samengevat (`summary_chunk_chars`) en daarna samengevoegd tot één verslag. Dat houdt kleine lokale modellen en de limieten van gratis tiers binnen hun grenzen.

## Ontwikkeling

```bash
pip install -e ".[dev]"
pytest
```

Getest: sneltoets-logica, opkuis, snippets, woordenboek, audio-resampling en knippunten, echo-filter, de volledige dicteer- en vergaderflow met nep-transcriptie en nep-LLM. Niet automatisch getest: echte microfoon, WASAPI-loopback, globale sneltoetsen en plakken; die vergen een Windows-sessie.

## Opname en toestemming

Een vergadering opnemen vereist de toestemming van de deelnemers. Meld de opname bij de start.
