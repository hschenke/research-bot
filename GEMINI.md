# Projekt-Richtlinien für Research-Bot

## Automatischer Start-Ablauf
- **Automatisches `git pull`**: Führe zu Beginn jeder neuen Konversation in diesem Projekt immer zuerst `git pull` aus, um das lokale Repository auf den neuesten Stand zu bringen, und gib kurz Rückmeldung.

## Automatischer Abschluss-Ablauf
- **Qualitätscheck vor jedem Commit**: Führe vor jedem Commit `ruff check .` und `pytest` in `.venv` aus.
- **Automatisches Committen & `git push`**: Committe abgeschlossene Features/Fixes sauber und pushe sie direkt nach `origin main`, damit GitHub Actions und Streamlit Community Cloud aktuell sind.
- **Automatisches Tagging & Push (Semantic Versioning)**: Erhöhe bei neuen Meilensteinen die Version in `src/__version__.py`, erstelle selbständig den passenden SemVer-Tag (`v0.X.0` für Features, `v0.X.Y` für Fixes) und pushe sowohl Commit als auch Tag direkt nach `origin`.

## Architektur & Clean Code Standards (Strikte Pflicht)
- **Frühzeitige Modularisierung**: Dateien kompakt halten (Ziel: < 200 Zeilen pro Modul), Funktionen klein halten (Ziel: max. 20–30 Zeilen, SRP, Guard Clauses statt tiefer Verschachtelung).
- **Strikte Schichten-Trennung**:
  1. `src/domain`: Reine, unveränderliche Datenmodelle, Enums und typisierte Exceptions.
  2. `src/security`: SSRF-Guard (inkl. DNS-Rebinding- & Redirect-Prüfung), Input-Whitelist-Validierung, HMAC-Auth & Brute-Force-Schutz.
  3. `src/storage`: `SqlExecutor`-Abstraktion + einheitliches `SqlResearchRepository` ohne dupliziertes SQL für SQLite & Turso (Hrana v2 HTTPS).
  4. `src/research`: Moderne `google-genai` Interactions API (`>=2.30.1`), SSRF-geschützter Web-Fetcher, Planner, Investigator, Synthesizer & Checkpoint-fähiger Orchestrator.
  5. `src/worker`: Entkoppelte Hintergrund-Ausführung (>1h via GitHub Actions `workflow_dispatch` + Watchdog-Cron sowie lokaler `ThreadPoolExecutor` Fallback).
  6. `src/notifications`: Sicheres HTML-E-Mail-Rendering & Versand via Resend / SMTP.
  7. `src/ui`: Reine Streamlit-Präsentationsschicht ohne Geschäfts-, DB- oder Netzwerklogik.

## Security Baseline (`security-standard`)
- **Zero Hardcoded Secrets**: Alle Keys/Passwörter ausschließlich über Umgebungsvariablen / `st.secrets` (`repr=False` in Settings-Objekten).
- **Parameterized SQL**: Ausnahmslos `?`-Platzhalter für SQLite und Turso.
- **SSRF & DoS Prevention**: Strikte IP-/Schema-Prüfung vor jedem HTTP-Abruf, Redirect-Validierung, explizite Timeouts und Payload-Größenlimits.
- **Safe Rendering**: Kein ungeprüftes Roh-HTML mit Usereingaben; alle Markdown-Links und HTML-Ausgaben strikt bereinigen.
