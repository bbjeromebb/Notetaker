# Analyse Wispr Flow en vertaling naar Notetaker

## Aanpak

Notetaker is een functionele herbouw (clean-room): het gedrag van Wispr Flow is afgeleid uit publieke documentatie en reviews, niet uit gedecompileerde code. Decompileren of netwerkverkeer van de app ontleden valt onder de gebruiksvoorwaarden van Wispr en levert voor dit doel weinig op: de bouwstenen (spraakherkenning, LLM-opkuis, klembord-invoeging) zijn standaardtechnieken.

## Hoe Wispr Flow werkt (publieke bronnen)

| Functie | Gedrag in Wispr Flow | Bron |
|---|---|---|
| Push-to-talk | Sneltoets vasthouden, praten, loslaten; tekst verschijnt in het actieve veld | docs.wisprflow.ai |
| Hands-free | Dubbeltik op de sneltoets start en stopt opname zonder vasthouden | docs.wisprflow.ai, sidsaladi.substack.com |
| Opkuis | LLM verwijdert stopwoorden, zet interpunctie, verwerkt zelfcorrecties | eesel.ai, tldv.io |
| Context | Toon past zich aan de app aan; vroegere versie stuurde screenshots naar de cloud (privacy-incident 2025), nu beperkte tekst via accessibility-API | laxis.com, bossai.tech |
| Command Mode | Geselecteerde tekst bewerken met een gesproken opdracht ("maak dit korter") | docs.wisprflow.ai |
| Woordenboek | Eigennamen en jargon, automatisch aanvullen mogelijk | docs.wisprflow.ai |
| Snippets | Spraaksnelkoppeling die uitbreidt naar vaste tekst (handtekening, link) | docs.wisprflow.ai |
| Verwerking | Cloud-only, geen offline modus; Privacy Mode = zero data retention bij LLM-leveranciers, standaard uit | laxis.com, yaps.ai |
| Vergaderingen | Geen kernfunctie van Wispr Flow; hier toegevoegd naar het model van Granola/Otter | - |

## Architectuur Notetaker

```
                 +-------------------- Dicteren ---------------------+
 sneltoets ----> | hotkey.ChordState (vasthouden/dubbeltik/commando) |
 (pynput)        +--------------------------+------------------------+
                                            |
 microfoon ----> audio.MicRecorder ---------+
                                            v
                 stt (faster-whisper lokaal | OpenAI-compatibele API)
                                            v
                 cleanup: snippets -> LLM-opkuis (stijl per app) -> woordenboek
                                            v
                 inserter: klembord + Ctrl+V, klembord herstellen

                 +------------------ Vergadering --------------------+
 microfoon ----> | kanaal "Ik"       \                               |
 systeemgeluid ->| kanaal "Anderen"   > chunks van 30 s, knip in stilte|
 (WASAPI-loopback)                   /  -> transcriptie per kanaal   |
                 | echo-filter -> samenvoegen op tijd -> live .md      |
                 | stop: LLM-verslag (samenvatting, beslissingen,      |
                 |       actiepunten, open vragen) + .wav              |
                 +---------------------------------------------------+
```

| Wispr-functie | Module | Verschil |
|---|---|---|
| Push-to-talk, hands-free | `hotkey.py` | Ctrl+Win vasthouden; dubbeltik of spatie = hands-free; Esc annuleert |
| Opkuis | `cleanup.py`, `llm.py` | Ollama lokaal (standaard), Groq/Gemini gratis tier of Claude; zonder LLM regelgebaseerde terugval |
| Context | `context.py` | Enkel procesnaam en venstertitel, geen scherminhoud |
| Command Mode | `dictation.py` | Ctrl+Win+Alt; selectie via Ctrl+C |
| Woordenboek | `config.toml` `dictionary`, `replacements` | Ook als hint aan Whisper (initial prompt) |
| Snippets | `cleanup.protect_snippets` | Placeholders beschermen de trigger tegen herformulering door het LLM |
| Geschiedenis | `history.jsonl` | Lokaal, met ruwe en opgeschoonde tekst en latency |
| Opnamevenster | `overlay.py` | Zwevende balk met toestand en niveaumeter, zonder focus te nemen |
| Vergaderingen | `meeting.py` | Extra; sprekerscheiding enkel jij versus de rest |
| Notities | `obsidian.py` | Extra; verslagen met frontmatter en taken in een Obsidian-vault naar keuze |

## Beperkingen

- Sprekerherkenning onderscheidt enkel microfoon en systeemgeluid. Echte diarisatie (pyannote) vergt een HuggingFace-token en een GPU voor redelijke snelheid.
- Lokale modellen zijn kleiner dan cloudmodellen: `gemma3:4b` kuist dictee goed op, maar volgt complexe instructies (commandomodus) minder strikt dan een groot model.
- Lokale `large-v3-turbo` op CPU: enkele seconden latency per dictee. Met een NVIDIA-GPU of de API-backend (Groq) zakt dat onder één seconde.
- Opname van anderen in een vergadering vraagt hun toestemming (GDPR, art. 6). De app toont geen melding aan andere deelnemers.
- Ctrl+Win+Spatie wisselt in Windows de invoertaal; gebruik bij conflicten de dubbeltik.

## Bronnen

- https://docs.wisprflow.ai/articles/2612050838-supported-unsupported-keyboard-hotkey-shortcuts
- https://docs.wisprflow.ai/articles/5096240724-navigating-the-wispr-flow-app-desktop-ios-and-android
- https://sidsaladi.substack.com/p/wispr-flow-101-the-complete-guide
- https://eesel.ai/blog/wispr-flow-overview
- https://tldv.io/blog/wisprflow/
- https://laxis.com/blog/wispr-flow/
- https://bossai.tech/blog/wispr-flow-review
