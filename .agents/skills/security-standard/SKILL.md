---
name: security-standard
description: >-
  Use this skill whenever developing, reviewing, auditing, or refactoring code to ensure security best practices across Code Security, OWASP Top 10, and General Software Security. Mandatory baseline requirement for all development workflows, APIs, web applications, scrapers, data pipelines, and CLI tools.
---

# Security & Secure Development Standard

Dieser Skill definiert die verbindlichen Sicherheitsstandards für Entwicklung, Architektur, Refactoring und Code-Reviews über alle Entwicklungsprojekte und Programmiersprachen hinweg.

---

## 1. Code Security (Sichere Implementierung)

### Secrets & Credential Management
* **Niemals Hardcoded Secrets:** Keine Passwörter, API-Keys, Private Keys, SSH-Schlüssel, JWT-Secrets oder OAuth-Tokens im Quellcode oder im Versionsverlauf (Git).
* **Umgebungsvariablen & Secret Manager:** Verwende ausschließlich Umgebungsvariablen (`os.getenv`, `process.env`), `.env`-Dateien oder spezialisierte Secret-Manager (z. B. Google Cloud Secret Manager, Vault, AWS Secrets Manager, Streamlit Secrets `st.secrets`).
* **Git-Schutz:** `.env`, `.env.local`, Credentials-Dateien (`credentials.json`, `*.pem`, `*.key`) MÜSSEN ausnahmslos in `.gitignore` geführt werden.
* **Vorlagen (.env.example):** Verwende `.env.example` ausschließlich mit Dummy-Werten (`API_KEY=your_key_here`), niemals mit echten Secrets.
* **Git-Leaks Prevention:** Bei Verdacht auf versehentlich committete Secrets diese sofort widerrufen/rotieren und den Git-Verlauf bereinigen.

### Input-Validierung & Sanitization
* **Zero-Trust für Inputs:** Jede externe Eingabe (HTTP-Payloads, Query-Parameter, Header, Cookies, Web-Scraping-Daten, CLI-Argumente, Dateiinhalte) ist potentiell bösartig.
* **Whitelist vor Blacklist:** Validiere Formate, Längen, Wertebereiche und Zeichensätze strikt gegen eine Positivliste (Whitelist).
* **Strikte Datenmodelle:** Nutze Type Hints und Validierungsbibliotheken (z. B. Pydantic, Zod, Marshmallow, dataclasses), um Datenstrukturen direkt an der Systemgrenze zu validieren.
* **ReDoS-Vermeidung:** Keine regulären Ausdrücke mit exponentieller Laufzeit (nested quantifiers wie `(a+)+`).

### Output-Encoding & Injection-Prävention
* **SQL / NoSQL Injection:**
  * Niemals SQL-Strings mit String-Konkatenation, `.format()` oder f-Strings zusammenbauen.
  * Zwingend **Parameterized Queries** (Prepared Statements) oder etablierte ORMs (SQLAlchemy, Prisma, Hibernate) verwenden.
  * *Negativ-Beispiel:* `cursor.execute(f"SELECT * FROM users WHERE id = '{user_id}'")`
  * *Positiv-Beispiel:* `cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))`
* **Command Injection:**
  * Niemals `os.system()` oder Shell-Strings mit Usereingaben aufrufen.
  * Bei Subprozessen immer Argument-Listen und `shell=False` verwenden:
  * *Positiv-Beispiel:* `subprocess.run(["git", "checkout", branch_name], shell=False, check=True)`
* **Cross-Site Scripting (XSS):**
  * Kontextbezogenes Escaping aller Benutzereingaben vor der Ausgabe in HTML/DOM/Markdown.
  * In UI-Frameworks (React, Vue, Streamlit) Standard-Rendering nutzen und Roh-HTML-Rendering (`st.markdown(..., unsafe_allow_html=True)`, `dangerouslySetInnerHTML`) strikt vermeiden bzw. nur mit geprüften Sanitize-Bibliotheken (z. B. Bleach, DOMPurify) zulassen.

### Sichere Datei- & Pfad-Handhabung (Path Traversal & Safe I/O)
* **Path Traversal Schutz:** Verhindere Angriffe wie `../../etc/passwd`.
* **Pfad-Validierung:** Absolute Pfade immer mit `resolve()` auflösen und prüfen, ob sie innerhalb des Zielverzeichnisses liegen:
  ```python
  from pathlib import Path

  def get_safe_filepath(base_dir: Path, filename: str) -> Path:
      target_path = (base_dir / filename).resolve()
      if not target_path.is_relative_to(base_dir.resolve()):
          raise ValueError(f"Sicherheitsverletzung: Ungültiger Pfad '{filename}'")
      return target_path
  ```
* **Dateinamen säubern:** Unerwartete Steuerzeichen oder Pfad-Separatoren entfernen.
* **Sichere Dateirechte:** Sensible Dateien und Verzeichnisse mit restriktiven Dateirechten erstellen (z. B. `0o600` für sensible Daten, `tempfile.NamedTemporaryFile`).

### Sichere Serialisierung & Deserialisierung
* **Verbot unsicherer Parser:** Verwende NIEMALS `pickle.loads()`, `marshal`, `shelve` oder `yaml.load()` mit untrusted / externen Daten (Remote Code Execution Risiko).
* **Sichere Alternativen:** Verwende standardisiertes JSON (`json.loads()`), `yaml.safe_load()` oder MessagePack/Protobuf.

### Ressourcenschutz & DoS-Prävention
* **Explizite Timeouts:** Jede Netzwerk- und I/O-Operation (HTTP, DB, Cache, Subprocess) MUSS einen expliziten Timeout definieren (z. B. `requests.get(url, timeout=(3.0, 10.0))`).
* **Payload- und Request-Limits:** Begrenze maximale Dateigrößen und Body-Größen (`MAX_CONTENT_LENGTH`), um Speichererschöpfung (Out of Memory) zu verhindern.
* **Pagination & Rate Limiting:** Keine unbegrenzten Abfragen (`SELECT * FROM logs` ohne LIMIT).

---

## 2. OWASP Top 10 Security (Web & API Protection)

### A01: Broken Access Control
* **Principle of Least Privilege:** Standardmäßig jeden Zugriff verweigern (Default Deny).
* **Server-side Authorization:** Verifiziere Berechtigungen bei jeder einzelnen Aktion serverseitig; verlasse dich niemals auf client-seitige UI-Restriktionen oder versteckte Buttons.
* **Schutz vor IDOR (Insecure Direct Object References):** Stelle sicher, dass `user_id` oder Tenant-ID bei allen Datenzugriffen validiert wird (`WHERE id = :id AND tenant_id = :tenant_id`).

### A02: Cryptographic Failures
* **Moderne Kryptographie:** Verwende nur anerkannte, zeitgemäße Standards (z. B. AES-256-GCM, ChaCha20-Poly1305).
* **Passwort-Hashing:** Verwende dedizierte adaptive Hash-Verfahren mit Salt: **Argon2id** (empfohlen) oder **bcrypt**. Veraltete Hashfunktionen (MD5, SHA1, einfaches SHA256) sind für Passwörter strikt verboten.
* **Verschlüsselung in Transit & at Rest:** HTTPS / TLS 1.3 erzwingen; sensible Daten verschlüsselt speichern.
* **Kryptografisch sichere Zufallswerte:** Verwende `secrets` (Python) oder `crypto.getRandomValues()` (JS), niemals Pseudozufall (`random.random()`) für Tokens oder Passwörter.

### A03: Injection
* Siehe Abschnitt 1: Konsequente Parameterisierung aller Datenzugriffe und Commandos.

### A04: Insecure Design
* **Threat Modeling:** Sicherheitsanforderungen von Anfang an in das Architekturdesign einbetten.
* **Fail-Secure:** Schlägt eine Prüfung fehl, verharrt das System im sicheren Zustand (Access Denied).

### A05: Security Misconfiguration
* **Debug-Modus abschalten:** In Produktionsumgebungen `DEBUG = False`, Test-Endpoints und Mock-Daten deaktivieren.
* **Security Headers setzen:**
  * `Content-Security-Policy (CSP)`
  * `X-Content-Type-Options: nosniff`
  * `X-Frame-Options: DENY` oder `SAMEORIGIN`
  * `Strict-Transport-Security (HSTS)`
* **Restriktives CORS:** Kein universelles `Access-Control-Allow-Origin: *` für sensitive APIs mit Credentials.
* **Detaillierte Fehlermeldungen verbergen:** Dem Client nur generische Fehler anzeigen; Stacktraces gehören ausschließlich in interne Logs.

### A06: Vulnerable and Outdated Components (SCA)
* **Lockfiles verwenden:** Abhängigkeiten via Lockfiles fixieren (`requirements.lock`, `poetry.lock`, `package-lock.json`).
* **Automatisierte Audits:** Regelmäßige Scans mit `pip-audit`, `safety`, `npm audit` oder Dependabot durchführen.
* **Sofortiges Patching:** Bekannte CVEs mit hohem/kritischem CVSS-Score sofort aktualisieren.

### A07: Identification and Authentication Failures
* **Brute-Force-Schutz:** Rate Limiting und Account-Lockout nach Fehlversuchen.
* **Sicheres Session-Management:**
  * Session-Tokens kryptografisch sicher generieren.
  * Cookies mit Flags `HttpOnly`, `Secure` und `SameSite=Lax` oder `Strict` versehen.

### A08: Software and Data Integrity Failures
* **Integritätsprüfung:** Hash-Verifizierung (SHA256) bei Downloads externer Bibliotheken oder Dateien.
* **Kein ungeprüftes Remote-Code-Laden:** Kein `curl | bash` in Build-Pipelines oder Init-Skripten.

### A09: Security Logging & Monitoring Failures
* **Sicherheitsereignisse loggen:** Authentifizierungsversuche, Rechteverletzungen und verdächtige Zugriffe nachvollziehbar protokollieren.
* **Log Sanitization (Data Leakage Prevention):** NIEMALS sensible Daten in Logs schreiben:
  * Keine Passwörter, API-Keys, Auth-Header (`Authorization: Bearer ...`).
  * Keine schutzbedürftigen personenbezogenen Daten (PII), Kreditkartennummern oder Sozialversicherungsnummern.

### A10: Server-Side Request Forgery (SSRF) – Speziell für Scraper & Aggregatoren
* **Schema-Whitelisting:** Nur `http` und `https` erlauben (kein `file://`, `gopher://`, `ftp://`).
* **Verbot interner & privater IP-Bereiche:** Blockiere Anfragen an:
  * Loopback: `127.0.0.0/8`, `localhost`, `::1`
  * Private RFC-1918-Netze: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
  * Link-Local / Cloud-Metadaten: `169.254.169.254`, `169.254.0.0/16`, `fe80::/10`
* **DNS-Rebinding-Schutz:** Hostname auflösen und die resultierende Ziel-IP vor dem Request validieren.

---

## 3. Allgemeine Security-Prinzipien & Governance

### Principle of Least Privilege (PoLP)
* Prozesse, Benutzerkonten und API-Clients dürfen nur die minimal benötigten Berechtigungen besitzen.
* Datenbank-Zugänge mit lesenden vs. schreibenden Rollen trennen.

### Defense in Depth (Mehrschichtige Absicherung)
* Verlasse dich niemals auf eine einzelne Verteidigungslinie.
* Beispiel: Netzwerk-Firewall + API-Gateway-Auth + Anwendungs-Autorisierung + DB-Parameterisierung + Verschlüsselung.

### Privacy by Design & Datensparsamkeit (DSGVO / GDPR)
* Erhebe nur Daten, die für die fachliche Funktion zwingend erforderlich sind.
* Löschfristen und Datenbereinigung automatisieren.
* Anonymisierung oder Pseudonymisierung sensibler Datensätze bei Analysen und Caching.

---

## 4. Security-Review Checkliste für jede Codeänderung

Vor jedem Abschluss einer Aufgabe muss folgende Checkliste durchlaufen werden:

1. [ ] **Secrets Check:** Keine Secrets/Credentials im Code oder Git-Diff?
2. [ ] **Input & Injection Check:** Werden alle externen Parameter validiert und Injection-Vektoren (SQL, Shell, XSS) verhindert?
3. [ ] **SSRF & Network Check:** Sind Timeouts gesetzt und Zugriffe auf interne IPs blockiert?
4. [ ] **Path Traversal Check:** Werden Dateipfade mit `resolve()` und `is_relative_to()` abgesichert?
5. [ ] **Logging & Privacy Check:** Werden keine PII, Passwörter oder Tokens in die Logs ausgegeben?
6. [ ] **Dependency Check:** Wurden neue Abhängigkeiten auf Aktualität und bekannte Schwachstellen geprüft?
