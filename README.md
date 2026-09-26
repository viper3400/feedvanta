# FeedVanta

FeedVanta lädt RSS-/Atom-Feeds regelmäßig, speichert Artikel lokal, filtert sie und veröffentlicht pro Quelle einen neuen RSS-Feed. Die Ausgabe kann direkt in NetNewsWire abonniert werden.

## Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
uvicorn app.main:app --reload
```

Danach die Verwaltung unter <http://127.0.0.1:8000> öffnen. Die SQLite-Datei wird standardmäßig als `data/feedvanta.db` angelegt. Mit `FEEDVANTA_DB=/anderer/pfad.db` lässt sich der Ort ändern.

### Betrieb unter einem Unterpfad

Die Anwendung lädt beim Start automatisch eine `.env`-Datei. Kopiere die Vorlage und passe sie bei Bedarf an:

```bash
cp .env.example .env
```

Mit `FEEDVANTA_BASE_PATH` kann die gesamte Anwendung unter einem Unterpfad veröffentlicht werden. Anschließend reicht der normale Startbefehl:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Die Verwaltung liegt dann unter `http://server:8000/feedvanta/`, ein Feed beispielsweise unter `http://server:8000/feedvanta/feed/1.xml`. Der konfigurierte Pfad muss vom Reverse Proxy unverändert an Uvicorn weitergereicht werden.

## Anmelden mit Google einrichten

Die Feedverwaltung und ihre API sind durch Google-Anmeldung und die Berechtigung `Global Administration` geschützt. Der **erste Benutzer**, der sich erfolgreich anmeldet, erhält diese Berechtigung automatisch. Benutzer und Berechtigungen werden in SQLite gespeichert und bleiben nach einem Neustart erhalten. Globale Administratoren können die Berechtigung in der Verwaltung weiteren angemeldeten Benutzern geben oder entziehen; die letzte globale Administration kann nicht entfernt werden.

Die generierten RSS-Feeds unter `/feed/{id}.xml`, die Kontrollansicht unter `/reader/{id}` und `/health` bleiben bewusst ohne Anmeldung erreichbar.

1. Öffne die [Google Auth Platform](https://console.cloud.google.com/auth/overview), wähle ein bestehendes Google-Cloud-Projekt oder erstelle eines und richte unter **Branding** den App-Namen, die Support-E-Mail und die Kontaktadresse ein.
2. Wähle unter **Audience**, ob die Anwendung nur für deine Google-Workspace-Organisation (**Internal**) oder für Google-Konten außerhalb der Organisation (**External**) verfügbar sein soll. Im externen Testmodus musst du die erlaubten Konten als Testnutzer eintragen.
3. Erstelle unter **Clients** einen neuen OAuth-Client vom Typ **Web application**.
4. Hinterlege unter **Authorized redirect URIs** die vollständige Callback-Adresse. Sie muss einschließlich Schema, Host, Port und Unterpfad exakt übereinstimmen:

   ```text
   https://example.com/feedvanta/auth/google/callback
   ```

   Für eine lokale Einrichtung ohne Unterpfad ist beispielsweise Folgendes möglich:

   ```text
   http://localhost:8000/auth/google/callback
   ```

5. Kopiere Client-ID und Client-Secret in `.env` und erzeuge ein dauerhaftes Session-Secret:

   ```dotenv
   GOOGLE_CLIENT_ID=123456789.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   FEEDVANTA_SESSION_SECRET=replace-with-the-output-of-openssl
   FEEDVANTA_SECURE_COOKIES=true
   ```

   ```bash
   openssl rand -hex 32
   ```

6. Starte FeedVanta neu. Unter `/feedvanta/` erscheint nun **Anmelden mit Google**.

Für eine öffentliche Installation verlangt Google HTTPS; nur localhost ist für lokale Tests davon ausgenommen. Wenn ein Reverse Proxy HTTPS beendet, muss er Host und Protokoll korrekt über `Forwarded` oder `X-Forwarded-*` weitergeben, damit FeedVanta die registrierte HTTPS-Callback-Adresse erzeugt. Client-Secret, Session-Secret und die lokale `.env` dürfen nicht ins Repository gelangen. Die Redirect-URI-Regeln und der Webserver-Flow sind in Googles offizieller Dokumentation beschrieben: [OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server) und [Manage OAuth Clients](https://support.google.com/cloud/answer/15549257).

## Versionierte Feedkonfiguration

FeedVanta unterscheidet drei unabhängige Versionen:

- **App-Version** (`app_version`): Version des laufenden FeedVanta-Codes, derzeit `0.1.0`.
- **Schema-Version** (`schema_version`): Struktur des JSON-Konfigurationsformats. Ein Import ist nur bei unterstützter Schema-Version möglich.
- **Config-Version** (`config_version`): Revision der konkreten Feedkonfiguration. Sie wird bei jedem Hinzufügen oder Löschen eines Feeds und bei jeder Änderung der Filterregeln erhöht. Beim Import wird die Revision der importierten Konfiguration übernommen.

Globale Administratoren erreichen Import und Export über **Konfiguration** in der Weboberfläche. Alternativ stehen die geschützten Endpunkte `GET /api/config/export` und `POST /api/config/import` zur Verfügung. Ein Import ersetzt die bestehende Feedkonfiguration vollständig.

Das Exportformat enthält ausschließlich Feeds und Filterregeln:

```json
{
  "app_version": "0.1.0",
  "schema_version": 1,
  "config_version": 12,
  "exported_at": "2026-09-26T10:00:00+00:00",
  "feeds": [
    {
      "name": "Example",
      "source_url": "https://example.com/feed.xml",
      "enabled": true,
      "refresh_interval": 30,
      "rules": [
        {
          "field": "title",
          "operator": "contains",
          "value": "Sport",
          "action": "exclude",
          "enabled": true
        }
      ]
    }
  ]
}
```

Benutzer, Berechtigungen, OAuth-Daten, Sitzungen, heruntergeladene Artikel sowie abgeleitete Feed-Metadaten werden niemals mitexportiert.

## Filterlogik

- Eine passende `exclude`-Regel blendet einen Artikel aus.
- Gibt es aktive `include`-Regeln, muss mindestens eine davon passen.
- Ohne `include`-Regel bleiben alle nicht ausgeschlossenen Artikel sichtbar.
- Vergleiche sind ohne Beachtung der Groß-/Kleinschreibung; Regex nutzt Python-Syntax.
- Neue oder geänderte Regeln werden sofort auf bereits gespeicherte Artikel angewendet.

Feeds werden jede Minute auf ihre individuelle Aktualisierungsfrist geprüft. Zusätzlich gibt es in der Oberfläche eine manuelle Aktualisierung. HTTP-Fehler ändern den vorhandenen Artikelbestand nicht.

## API und RSS

- `GET /api/feeds` – Feeds samt Regeln auflisten
- `POST /api/feeds` – Feed als JSON anlegen
- `POST /api/feeds/{id}/rules` – Regel als JSON anlegen
- `POST /api/feeds/{id}/refresh` – Feed sofort abrufen
- `DELETE /api/feeds/{id}` – Feed löschen
- `DELETE /api/rules/{id}` – Regel löschen
- `GET /api/config/export` – versionierte Feedkonfiguration exportieren
- `POST /api/config/import` – versionierte Feedkonfiguration ersetzen
- `GET /feed/{id}.xml` – gefilterter RSS-Feed
- `GET /health` – Healthcheck

Beispiel:

```bash
curl -X POST http://127.0.0.1:8000/api/feeds \
  -H 'content-type: application/json' \
  -d '{"name":"Heise", "source_url":"https://www.heise.de/rss/heise-atom.xml"}'
```
