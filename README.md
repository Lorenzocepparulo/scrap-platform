# 🧠 Scrap Solovera — Piattaforma di scraping orchestrata

Mini-piattaforma web interna su **https://scrap.solovera.work** che orchestra due
tool di scraping esistenti dietro un'interfaccia comune, con dashboard per
lanciare ricerche e ricevere i dati.

## Cosa fa

| Sezione | Tool | Output |
|---|---|---|
| **A. Monitoraggio portali immobiliari** (ricorrente) | Idealista + Immobiliare.it (Python) | Google Sheets con tracking nuovi/ribassi/venduti |
| **B. Scraping Google Maps** (one-shot) | gosom/google-maps-scraper (Go, binario) | File Excel (.xlsx) scaricabile |

## Architettura (modulare)

```
┌─────────────────────────────────────────────────────────┐
│  Dashboard web (HTML + Tailwind)                        │
│  ├─ Sezione A: monitor portali immobiliari              │
│  ├─ Sezione B: scraping Google Maps one-shot            │
│  ├─ Job attivi (pausa/riprendi/elimina)                 │
│  └─ Log errori visibili                                 │
└──────────────┬──────────────────────────────────────────┘
               │ FastAPI
┌──────────────▼──────────────────────────────────────────┐
│  Orchestratore (app/main.py + app/monitor.py)           │
│  • SQLite: config job + stato annunci (app/db.py)       │
│  • APScheduler: job ricorrenti (app/scheduler.py)       │
│  • Anti-bot: UA rotanti + delay random + proxy (app/anti_bot.py) │
└───────┬──────────────────────────────┬──────────────────┘
        │                              │
┌───────▼───────────┐        ┌─────────▼─────────────────┐
│ PORTALI (Python)  │        │ GOOGLE MAPS (Go)          │
│ app/scrapers/     │        │ app/scrapers/gmaps.py     │
│ ├─ base.py        │        │ → subprocess gm_scraper   │
│ │  (interfaccia)  │        │ → CSV → Excel (pandas)    │
│ ├─ idealista.py   │        └───────────────────────────┘
│ ├─ immobiliare.py │
│ └─ registry.py    │
└───────┬───────────┘
        │ gspread (service account)
┌───────▼────────────────────────────────────────────────┐
│ GOOGLE SHEET (fogli: Database, Nuovi, Ribassi,         │
│ Venduti-Rimossi, Log)                                  │
└────────────────────────────────────────────────────────┘
```

**Regola d'oro della modularità:** ogni portale implementa la stessa interfaccia
`PortalScraper` (in `app/scrapers/base.py`). Se un portale cambia l'HTML, si
sistema **solo** il suo adapter (`idealista.py` / `immobiliare.py`) senza toccare
orchestratore, DB, dashboard o Sheets. Per aggiungere un portale nuovo: crea un
adapter e registralo in `registry.py`.

## Sezione A — Monitoraggio portali immobiliari

**Input:**
- Portale: Immobiliare.it e/o Idealista
- URL della ricerca **già filtrata** sul portale (metodo primario, più robusto)
- Frequenza: default ogni 24 ore
- Campi da tracciare (checkbox): prezzo, tipologia, superficie mq, locali, bagni,
  piano, indirizzo/zona, contratto, privato/agenzia, nome agenzia, riferimento,
  URL, data pubblicazione

**Comportamento (il cuore):** ogni run fa scraping della zona, estrae un **ID
univoco** per ogni annuncio (dall'URL) e confronta con lo stato salvato nel DB:

- **ID nuovo** → "nuovo immobile", registra `data_primo_avvistamento`
- **ID presente e ancora online** → aggiorna `data_ultimo_avvistamento`; se il
  prezzo è cambiato → registra un **RIBASSO** (data, prezzo vecchio → nuovo,
  contatore incrementato)
- **ID che c'era ma ora manca** → "non più online" (presunta vendita/ritiro),
  calcola `giorni_online = data_ultimo − data_primo`, registra `data_scomparsa`

**Google Sheet** (stesso file per tutti i run, creato al primo run — il link
viene mostrato in dashboard):
- `Database` — una riga per annuncio con tutti i campi + prezzo_iniziale,
  prezzo_attuale, num_ribassi, date, giorni_online, stato
- `Nuovi` — annunci comparsi nell'ultimo run
- `Ribassi` — ribassi rilevati (annuncio, data, vecchio prezzo, nuovo prezzo, %)
- `Venduti-Rimossi` — annunci scomparsi con giorni online
- `Log` — timestamp e riepilogo di ogni esecuzione

## Sezione B — Scraping Google Maps (one-shot)

**Input:** query libera + area (es. "agenzie immobiliari Lombardia"), campi
checkbox (nome, categoria, indirizzo, telefono, sito web, email, rating,
recensioni, orari, coordinate).

**Comportamento:** lancia `gm_scraper` (gosom, binario Go scaricato nel
Dockerfile) con i parametri, genera un `.xlsx` scaricabile dalla dashboard e
finisce lì. Nessun tracking, nessuno storico.

## Anti-bot

- User-agent rotanti (7 UA configurati in `app/config.py`)
- Delay randomici tra le richieste (`SCRAP_DELAY_MIN`/`SCRAP_DELAY_MAX`, default 2.5–6s)
- Proxy opzionale via env `SCRAP_PROXY`
- Limiti: `SCRAP_MAX_PAGES` (default 3) e `SCRAP_MAX_ANNUNCI` (default 60)

## Avvio rapido (locale)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SCRAP_DATA_DIR=./data
cp .env.example .env   # poi compila .env
uvicorn app.main:app --port 8000
```

→ http://localhost:8000 (password = `SCRAP_PASSWORD`)

## Avvio con Docker (consigliato)

```bash
cp .env.example .env   # compila SCRAP_PASSWORD e GOOGLE_SERVICE_ACCOUNT_JSON
docker compose up -d --build
```

L'immagine è già pubblica su GHCR: `ghcr.io/lorenzocepparulo/scrap-platform:latest`
(build automatica da GitHub Actions su push a `main`).

## Come configurare il service account Google (per Google Sheets)

1. Vai su https://console.cloud.google.com/ → crea un progetto (o usane uno esistente)
2. **API & Services → Library** → abilita **Google Sheets API**
3. **API & Services → Credentials → Create credentials → Service account**
4. Nel service account: **Keys → Add key → JSON** → scarichi il file
5. Prendi il **contenuto del JSON** e mettilo nella variabile
   `GOOGLE_SERVICE_ACCOUNT_JSON` del `.env` (tutto su una riga, oppure in base64)
6. `SCRAP_SHEET_SHARE_EMAIL`: la tua email, così lo spreadsheet creato dal
   primo run ti viene condiviso automaticamente in modifica

> Senza service account la piattaforma funziona lo stesso (i dati restano in
> SQLite e sono visibili in dashboard), ma il sync su Google Sheets viene
> saltato con un errore visibile nel Log.

## Deploy su VPS (Hostinger + Traefik)

Il VPS (`srv1708775.hstgr.cloud`) ha Docker + Traefik con Let's Encrypt. Il
compose in questo repo espone `scrap.solovera.work` via label Traefik. Per il
deploy si usa l'API Hostinger:

```bash
# 1. push dell'immagine (già automatico via GitHub Actions)
# 2. deploy del compose via API Hostinger (endpoint docker del VPS)
curl -X POST "https://developers.hostinger.com/api/vps/v1/virtual-machines/<VM_ID>/docker" \
  -H "Authorization: Bearer $HOSTINGER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_name":"scrap-platform","content":"<docker-compose.yml>"}'
```

3. DNS: record A `scrap.solovera.work` → IP del VPS (zona Cloudflare solovera.work)

## Endpoint API principali

| Metodo | Path | Descrizione |
|---|---|---|
| POST | `/api/monitors` | Crea monitor portale |
| GET | `/api/monitors` | Lista monitor + stato job |
| POST | `/api/monitors/{id}/run` | Esecuzione manuale (background) |
| POST | `/api/monitors/{id}/pause` `/resume` | Pausa/riprendi |
| DELETE | `/api/monitors/{id}` | Elimina |
| POST | `/api/maps` | Lancia scraping Google Maps |
| GET | `/api/maps/jobs` | Lista job Maps |
| GET | `/api/download/{file}` | Download Excel |
| GET | `/api/logs` | Log esecuzioni/errori |
| GET | `/api/health` | Health check |

## Struttura del progetto

```
scrap-platform/
├── app/
│   ├── main.py            # FastAPI + API + static
│   ├── config.py          # configurazione da env
│   ├── db.py              # SQLite (monitor, annunci, ribassi, log, jobs)
│   ├── anti_bot.py        # UA rotanti, delay, proxy
│   ├── monitor.py         # logica run monitor (nuovi/ribassi/venduti)
│   ├── scheduler.py       # APScheduler
│   ├── sheets.py          # sync Google Sheets (gspread)
│   ├── scrapers/
│   │   ├── base.py        # ★ interfaccia comune PortalScraper
│   │   ├── idealista.py   # adapter Idealista
│   │   ├── immobiliare.py # adapter Immobiliare.it
│   │   ├── registry.py    # registro portali
│   │   └── gmaps.py       # wrapper gosom → Excel
│   └── static/            # dashboard (index.html, login.html)
├── data/                  # SQLite + download (volume)
├── Dockerfile             # python:3.12 + binario gosom
├── docker-compose.yml     # + label Traefik
├── .env.example
└── .github/workflows/     # build immagine → GHCR
```
