---
name: python-clean-code
description: >-
  Use this skill when reviewing, refactoring, or writing Python code according to Clean Code principles, PEP 8, strict type annotations, robust error handling, dataclasses, and idiomatic Python patterns. Ideal for code audits, modular restructuring, or news aggregator and scraper pipelines.
---

# Python Clean Code & Refactoring Guide

Dieser Skill führt eine strukturierte Qualitätsprüfung, ein Clean-Code-Review oder ein Refactoring für Python-Code durch – speziell optimiert für Datenverarbeitung, Scraping, News-Pipelines und moderne Web/Streamlit-Apps.

---

## 1. Pythonic Clean Code Richtlinien

### Typisierung & Datenmodelle
* **Moderne Type Hints (PEP 585 / PEP 604):** Verwende `str | None` statt `Optional[str]` und native Collections wie `list[str]`, `dict[str, Any]` (ab Python 3.10+).
* **Strukturierte Daten statt lose Dictionaries:**
  * Verwende `@dataclass(frozen=True)` oder Pydantic `BaseModel` für strukturierte Datensätze (z. B. `Article`, `NewsFeed`, `ScrapeResult`).
  * Keine magischen Dictionary-Schlüssel (`item["pub_dt_raw"]`), sondern typisierte Attribute (`article.published_at`).

### Fehlerbehandlung & Netzwerk-Resilienz
* **Keine Silent Fails:** Niemals `except: pass` oder bloßes `except Exception:` ohne detailliertes Logging.
* **Eigene Exception-Hierarchie:**
  ```python
  class NewsAggregatorError(Exception):
      """Basisklasse für alle Fehler der App."""
      pass

  class FeedFetchError(NewsAggregatorError):
      """Fehler beim Abrufen externer Feeds."""
      pass
  ```
* **Netzwerk-Robustheit (Scraping & APIs):**
  * Jeder HTTP-Call (`requests.get`, `httpx.get`) MUSS einen expliziten `timeout` haben.
  * Verwende Retry-Strategien mit Exponential Backoff (z. B. via `urllib3.util.Retry` oder `tenacity`).
  * Einzelne fehlschlagende Feeds dürfen niemals den gesamten Durchlauf abbrechen; fange Fehler isoliert ab und logge sie.

### Logging statt `print()`
* Verwende das Standard-Modul `logging` (`logger = logging.getLogger(__name__)`).
* Wähle die passende Log-Stufe:
  * `DEBUG`: Detaillierte Parsing-Schritte, URLs, Latenzen.
  * `INFO`: Meilensteine (z. B. "5 Feeds erfolgreich geladen, 42 Artikel gefunden").
  * `WARNING`: Ein Feed antwortet mit 404/500, Fallback wird genutzt.
  * `ERROR`: Kritische Verarbeitungsfehler mit Exception-Traceback (`logger.exception(...)`).

### Trennung von Belangen (Architecture & Separation of Concerns)
Strikte Trennung nach Schichten:
1. **Ingestion / Scraper:** Reines Abrufen und Parsen von externen Quellen (RSS, HTML, APIs).
2. **Processing / Transformation:** Filtern, Deduplizieren, Bereinigen, KI-Zusammenfassungen.
3. **Storage / Persistence:** Speichern und Laden (Dateisystem, JSON, SQLite, Cache).
4. **Presentation / UI:** Streamlit-Komponenten oder CLI-Ausgaben – die UI enthält keine Business-Logik oder Parsing-Code!

---

## 2. Refactoring-Workflow: Schritt für Schritt

Wenn dieser Skill aufgerufen wird, gehe nach folgendem Ablauf vor:

### Schritt 1: Analyse & Code-Smell-Audit
Untersuche die Zieldatei(en) auf folgende typische Code Smells:
* **God-Funktionen:** Funktionen mit mehr als 30–40 Zeilen oder mehreren Aufgaben (SRP-Verletzung).
* **Tiefe Verschachtelung:** Mehrere `for`/`if`-Ebenen (durch Guard Clauses und Hilfsfunktionen entzerren).
* **Mutable Default Arguments:** `def func(items=[])` vermeiden (`None` als Default und im Body initialisieren).
* **Fehlendes Ressourcen-Management:** Dateien oder Sessions ohne `with`-Context-Manager.
* **Mischung von UI und Logik:** Streamlit-Aufrufe (`st.write`, `st.button`) vermischt mit HTTP-Requests.

### Schritt 2: Refactoring-Plan
Erstelle einen kurzen, übersichtlichen Plan vor der Durchführung:
* Welche Klassen/Dataclasses werden eingeführt?
* Welche Teilfunktionen werden extrahiert?
* Welche Schnittstellen bleiben unverändert (Verhalten muss abwärtskompatibel bleiben)?

### Schritt 3: Durchführung
* Wende die Änderungen schrittweise an.
* Behalte bestehende Docstrings und Typisierungen bei bzw. erweitere sie.

### Schritt 4: Verifikation & Tests
* Führe vorhandene Tests aus (z. B. `pytest`).
* Führe ggf. Linter oder Typ-Checks aus (`ruff`, `flake8`, `mypy` falls konfiguriert).
* Bestätige, dass alle Importe sauber und ohne Zyklen aufgelöst werden.
