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
        │ Maton API (google-sheets OAuth)
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
cp .env.example .env   # compila SCRAP_PASSWORD e MATON_API_KEY
docker compose up -d --build
```

L'immagine è già pubblica su GHCR: `ghcr.io/lorenzocepparulo/scrap-platform:latest`
(build automatica da GitHub Actions su push a `main`).

## Google Sheets — via Maton (nessun service account)

Il sync usa la **MATON_API_KEY** (la stessa di Gmail/Drive/Stripe): la connessione
`google-sheets` su Maton è già collegata con l'account `psigewebmarketing@gmail.com`,
quindi la piattaforma può creare e aggiornare spreadsheet direttamente.

- Variabile richiesta: `MATON_API_KEY` (nel `.env` / nel deploy)
- `SCRAP_SHEET_SHARE_EMAIL` (opzionale): email a cui condividere lo spreadsheet creato dal primo run
- Il primo run crea lo spreadsheet (fogli: Database, Nuovi, Ribassi, Venduti-Rimossi, Log) e salva il link sul monitor; i run successivi aggiornano lo stesso file.

> Fallback legacy (non necessario): `GOOGLE_SERVICE_ACCOUNT_JSON` è ancora supportato nel
> codice se un giorno servisse, ma il percorso standard è Maton.

## Deploy su VPS (Hostinger + Traefik + Worker Cloudflare) — STATO ATTUALE: LIVE ✅

Il VPS (`srv1708775.hstgr.cloud`, 152.239.114.168) ha Docker + Traefik con Let's Encrypt.
**Architettura effettiva in produzione** (il token Cloudflare non ha permessi DNS edit, quindi non si può creare un record A diretto):

```
scrap.solovera.work
  └─ Cloudflare Worker "scrap-proxy" (custom domain, auto-DNS)
       └─ fetch → http://srv1708775.hstgr.cloud:18080   (hostname, NON IP: CF blocca i fetch a IP grezzi con errore 1003)
            └─ Traefik/VPS porta 18080 pubblicata → container scrap-platform:8000
```

1. **Repo**: `https://github.com/Lorenzocepparulo/scrap-platform` (pubblico, serve per il clone lato Hostinger)
2. **Build sul VPS**: `POST /api/vps/v1/virtual-machines/1708775/docker` con `content` = URL del repo GitHub (Hostinger clona e fa `docker compose up --build`)
3. **Worker proxy**: `npx wrangler deploy scrap-proxy-worker.js --name scrap-proxy` + `PUT /accounts/<ACCT>/workers/domains {hostname: scrap.solovera.work, service: scrap-proxy}`
4. **Firewall Hostinger**: porta TCP 18080 aperta (firewall id 352021)
5. **Password dashboard**: `SCRAP_PASSWORD` passata nel campo `environment` (stringa `KEY=VALUE\nKEY=VALUE`) del deploy API

### Deploy rapido dopo una modifica

```bash
# 1. push su main (triggera anche build GHCR, ma non serve per il VPS)
git push origin main
# 2. redeploy sul VPS via API (content = repo URL)
curl -X POST "https://developers.hostinger.com/api/vps/v1/virtual-machines/1708775/docker" \
  -H "Authorization: Bearer $HOSTINGER_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"project_name":"scrap-platform","content":"https://github.com/Lorenzocepparulo/scrap-platform","environment":"SCRAP_PASSWORD=...\nSCRAP_SESSION_SECRET=..."}'
```

### ⚠️ Nota portali (Sezione A)
Idealista e Immobiliare.it proteggono con DataDome/Cloudflare: le richieste HTTP dirette (anche con browser headless) ricevono 403 dall'IP del VPS. Senza gateway anti-bot i monitor girano, salvano lo stato in SQLite e l'errore è **visibile nel Log della dashboard** (come da requisito). Per sbloccare lo scraping reale: configurare `SCRAP_ANTIBOT_URL` + `SCRAP_ANTIBOT_KEY` (es. ZenRows, come suggerisce il repo originale) oppure `SCRAP_PROXY` con proxy residenziali.

### ⚠️ Nota Google Sheets
Il sync su Sheets richiede solo `MATON_API_KEY` (connessione google-sheets già attiva su Maton). Senza la chiave, i monitor funzionano (dati in SQLite + dashboard) e l'errore è visibile nel Log.

## Deploy alternativo (Traefik diretto, se si avrà un token CF con DNS edit)

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
