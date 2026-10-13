# Notetaker

Dicteren in elke Windows-app en vergaderingen (ook Teams, Zoom of Meet) transcriberen en samenvatten. Functionele herbouw van Wispr Flow in Python, aangevuld met vergaderverslagen. Analyse en architectuur: [docs/analyse-wispr-flow.md](docs/analyse-wispr-flow.md).

## Installatie (Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
notetaker init-config
setx ANTHROPIC_API_KEY "sk-ant-..."
```

De eerste start downloadt het Whisper-model (ongeveer 1,6 GB voor `large-v3-turbo`). Met een NVIDIA-GPU: installeer CUDA 12 en cuDNN 9, dan kiest `device = "auto"` de GPU.

## Gebruik

| Actie | Toetsen |
|---|---|
| Dicteren | Ctrl+Win vasthouden, praten, loslaten |
| Hands-free | Dubbeltik Ctrl+Win (of Spatie tijdens vasthouden); opnieuw Ctrl+Win om te stoppen |
| Selectie herschrijven | Tekst selecteren, Ctrl+Win+Alt vasthouden, opdracht inspreken ("maak dit formeler") |
| Annuleren | Esc |

```powershell
notetaker run                         # systeemvak-app: dicteren + vergaderknop
notetaker meeting --title "Stuurgroep" # opname tot Ctrl+C, verslag in Documenten\Notetaker
notetaker transcribe opname.m4a --summarize
notetaker summarize verslag.md
notetaker devices                      # audioapparaten tonen
```

Een vergadering levert één Markdown-bestand op met samenvatting, besproken punten, beslissingen, actiepunten, open vragen en de volledige transcriptie met tijdstempels, plus een `.wav` van de opname. Tijdens de opname groeit de transcriptie live in hetzelfde bestand.

## Configuratie

`%APPDATA%\Notetaker\config.toml`, zie [config.example.toml](src/notetaker/config.example.toml): woordenboek, vaste correcties, snippets, stijl per app, sneltoetsen, modellen.

| Keuze | Privacy | Snelheid | Kost |
|---|---|---|---|
| `stt.backend = "faster-whisper"` | audio blijft lokaal | CPU traag, GPU snel | gratis |
| `stt.backend = "openai-api"` (Groq) | audio naar de cloud | zeer snel | per minuut |
| `llm.backend = "anthropic"` | tekst naar Anthropic | ~1 s per dictee | per token |
| `llm.backend = "none"` | volledig lokaal | direct | gratis, enkel basisopkuis |

Standaardmodel is `claude-opus-5-5` (opkuis op `effort = "low"`, verslag op `"high"`), met server-side fallback bij een weigering. Voor kortere latency bij dicteren: `cleanup_model = "claude-haiku-5-5"`.

## Ontwikkeling

```bash
pip install -e ".[dev]"
pytest
```

Getest: sneltoets-logica, opkuis, snippets, woordenboek, audio-resampling en knippunten, echo-filter, de volledige dicteer- en vergaderflow met nep-transcriptie en nep-LLM. Niet automatisch getest: echte microfoon, WASAPI-loopback, globale sneltoetsen en plakken; die vergen een Windows-sessie.

## Opname en toestemming

Een vergadering opnemen vereist de toestemming van de deelnemers. Meld de opname bij de start.
