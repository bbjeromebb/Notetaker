# Notetaker

Dicteren in elke Windows-app en vergaderingen (ook Teams, Zoom of Meet) transcriberen en samenvatten. Functionele herbouw van Wispr Flow in Python, aangevuld met vergaderverslagen. Analyse en architectuur: [docs/analyse-wispr-flow.md](docs/analyse-wispr-flow.md).

## Installatie (Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
notetaker init-config
```

Taalmodel (gratis, lokaal): installeer [Ollama](https://ollama.com) en haal de modellen op:

```powershell
ollama pull gemma3:4b    # opkuis van dictee, ~3,3 GB
ollama pull gemma3:12b   # vergaderverslagen, ~8 GB (minder dan 16 GB RAM: gebruik gemma3:4b)
```

De eerste start downloadt het Whisper-model (ongeveer 1,6 GB voor `large-v3-turbo`). Met een NVIDIA-GPU: installeer CUDA 12 en cuDNN 9, dan kiest `device = "auto"` de GPU. Spraakherkenning en taalmodel draaien dan allebei lokaal: geen kosten, geen audio of tekst naar buiten.

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
