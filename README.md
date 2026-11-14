# 🔬 Research-Bot

Ein autonomer, hintergrundfähiger **Deep-Web-Research-Bot**, der zu beliebigen Themen eigenständig Informationen aus dem Web zusammenträgt, Primärquellen auswertet, zu einem strukturierten Dossier aufbereitet und dich **automatisch per E-Mail informiert**, sobald das Ergebnis vorliegt – auch bei mehrstündigen Tiefenrecherchen (**> 1 Stunde Laufzeit**).

Konzipiert auf derselben kostenlosen, wartungsfreien Cloud-Infrastruktur wie `news-aggregator-bot` (**GitHub Actions + Streamlit Community Cloud + Turso LibSQL + Google Gemini + Resend/SMTP**) mit kompromisslosem Fokus auf **Clean Code, Stabilität, Performance und Security**.

---

## 🏗️ Architektur für > 1 Stunde Hintergrund-Laufzeit

Auf kostenlosen Web-Plattformen wie *Streamlit Community Cloud* werden inaktive Browser-Sessions oder Container nach einiger Zeit schlafen gelegt – ein einfacher Python-Thread in der Web-App würde bei einer 90-minütigen Recherche abbrechen, sobald du das Smartphone weglegst.

**Research-Bot entkoppelt deshalb Web-UI und Hintergrund-Worker sauber über Turso:**

```text
┌──────────────────────────────────────────────────────────────────────┐
│  1. Streamlit Web-App (Desktop / Smartphone)                         │
│     • Thema, Schwerpunkte & Tiefe wählen (Quick, Standard oder Deep)  │
│     • Speichert Auftrag in Turso (`queued`)                          │
│     • Triggert sofort GitHub Actions Worker via REST API             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  2. Turso Cloud DB (LibSQL via HTTPS Hrana v2) / Lokal: SQLite WAL   │
│     • Atomares Job-Claiming (verhindert Doppelausführung)            │
│     • Live-Fortschritt (%), Aktivitäts-Log & Heartbeats              │
│     • Checkpointing nach JEDEM Teilthema (Crash- & Resume-sicher!)   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  3. GitHub Actions Cloud-Worker (`timeout-minutes: 300` = 5 Std.)    │
│     • Phase 1: Recherche-Planung in N überschneidungsfreie Teilfragen│
│     • Phase 2: Gemini Deep Research Agent + Google Search Grounding  │
│     • Phase 3: SSRF-geschütztes Volltext-Scraping der Primärquellen  │
│     • Phase 4: Nachschärfungsrunden für offene Wissenslücken         │
│     • Phase 5: Dossier-Synthese mit Quellenverzeichnis               │
│     • Phase 6: Formatierte HTML-E-Mail mit signiertem Direkt-Link    │
└──────────────────────────────────────────────────────────────────────┘
```

> 💡 **Lokaler Fallback inklusive:** Startest du die App lokal ohne GitHub/Turso (`streamlit run streamlit_app.py`), übernimmt automatisch ein lokaler `ThreadPoolExecutor` mit lokaler SQLite-Datenbank (`data/research_bot.db`).

---

## 🛡️ Clean Code & Security Highlights

- **Strikte Schichtenarchitektur (SOLID)**: Klare Trennung in `domain`, `config`, `security`, `storage`, `research`, `worker`, `notifications` und `ui`. Keine Geschäfts-, SQL- oder Scraping-Logik in den Streamlit-Views.
- **Zero SQL Duplication**: Einheitliches `SqlResearchRepository` über ein schlankes `SqlExecutor`-Protokoll bedient sowohl lokales SQLite (WAL) als auch Turso Cloud (HTTPS Hrana v2) mit 100 % parametrisierten Queries (`?`).
- **SSRF- & DNS-Rebinding-Schutz (`ssrf_guard.py`)**: Prüft jede URL sowie **jeden HTTP-Redirect-Hop** vor dem Abruf gegen Loopback (`127.0.0.1`, `::1`), private RFC-1918-Netze, CGNAT und Cloud-Metadaten (`169.254.169.254`).
- **DoS- & Speicher-Schutz**: Strikte Connect-/Read-Timeouts bei allen Netzwerkaufrufen sowie hartes Streaming-Limit (`2 MB` pro Webseite).
- **Default-Deny Auth & Brute-Force-Schutz**: Ist `APP_PASSWORD` gesetzt, schützt eine Rate-Limited Login-Schranke die App. E-Mail-Benachrichtigungen enthalten ein kryptographisches **Read-Only HMAC-Token**, mit dem Berichte mobil mit einem Klick gelesen, aber keine Einstellungen verändert werden können.
- **Privacy & Secret Hygiene**: Alle Secrets tragen `repr=False`; E-Mail-Adressen werden in Logs datenschutzkonform maskiert (`h***@domain.de`).

---

## 📁 Projektstruktur

```text
research-bot/
├── .github/workflows/
│   ├── run_research.yaml          # Hintergrund-Worker (workflow_dispatch + Watchdog-Cron, 5h Timeout)
│   └── ci.yaml                    # Automatisches Linting (Ruff + Bandit) & Tests (Pytest)
├── .streamlit/
│   ├── config.toml                # Theme, XSRF-Schutz & sichere Server-Defaults
│   └── secrets.toml.example       # Vorlage für Streamlit Cloud Secrets
├── config/
│   └── settings.yaml              # Gesamte deklarative KI- & Fachlogik (Prompts, Stil, Formate, Profile, Limits)
├── src/
│   ├── config/                    # YAML-Domain-Loader, Konstanten & typisierte AppSettings
│   ├── domain/                    # Enums, Exception-Hierarchie, DomainConfig & unveränderliche Modelle
│   ├── security/                  # SSRF-Guard, Input-/YAML-Validator, HMAC-Auth & Rate-Limiter
│   ├── storage/                   # SqlExecutor (SQLite & Turso) + SqlResearchRepository + DomainConfigStore
│   ├── research/                  # Gemini Interactions API, WebFetcher, Planner, Investigator, Synthesizer, Orchestrator
│   ├── worker/                    # GitHubActionsDispatcher, LocalBackgroundRunner & CLI-Worker
│   ├── notifications/             # Responsiver HTML-Mail-Renderer & Resend/SMTP-Versand
│   └── ui/                        # Schlanke Streamlit-Views inkl. Live-Editor für Rules & Recovery (`🎛️ Rules`)
├── tests/                         # Umfassende Unit- & Integrationstests
├── streamlit_app.py               # Root-Entrypoint für Streamlit Cloud
├── requirements.txt               # Vollständige Abhängigkeiten (UI + Worker)
└── requirements-worker.txt        # Schlanke Worker-Abhängigkeiten für schnelle GitHub-Runs
```

---

## 🚀 Lokale Einrichtung & Start

```powershell
# 1. Virtuelle Umgebung anlegen & aktivieren (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Abhängigkeiten installieren
python -m pip install -r requirements.txt

# 3. Umgebungsvariablen konfigurieren
Copy-Item .env.example .env
# -> GEMINI_API_KEY in .env eintragen

# 4. Web-App starten
streamlit run streamlit_app.py
```

### Qualitätssicherung (Tests & Linter)
```powershell
ruff check .
pytest -v
```

---

## ☁️ Cloud-Setup (0 € / Monat)

### 1. Turso Cloud-Datenbank anlegen
1. Auf [turso.tech](https://turso.tech) neue Datenbank erstellen (z. B. `research-bot`).
2. `TURSO_URL` (`libsql://...`) und `TURSO_KEY` kopieren.

### 2. GitHub Repository Secrets setzen
Unter **GitHub -> Settings -> Secrets and variables -> Actions** anlegen:
- `GEMINI_API_KEY`
- `TURSO_URL` & `TURSO_KEY`
- `RESEND_API_KEY`, `EMAIL_TO` (und optional `EMAIL_FROM`)
- `APP_PASSWORD` & `STREAMLIT_APP_URL`

### 3. Streamlit Community Cloud verbinden
1. Auf [share.streamlit.io](https://share.streamlit.io) neue App aus `hschenke/research-bot` (`main`, `streamlit_app.py`) erstellen.
2. In **App Settings -> Secrets** die Werte aus `.streamlit/secrets.toml.example` eintragen (inkl. eines Fine-Grained `GITHUB_TOKEN` mit Berechtigung **Actions: Read and write** auf `hschenke/research-bot`, damit die Web-App Hintergrund-Workflows direkt auslösen kann).
