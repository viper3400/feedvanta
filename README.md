# FeedVanta

FeedVanta lädt RSS-/Atom-Feeds regelmäßig, speichert Artikel lokal, filtert sie und veröffentlicht pro Quelle einen neuen RSS-Feed. Die Ausgabe kann direkt in NetNewsWire abonniert werden.

## Start

```bash
uv sync --extra test
npm ci && npm run build:css
uv sync --project ../pyhost --all-packages
uv pip install --python ../pyhost/.venv/bin/python -e .
uv run --project ../pyhost pyhost --reload
```

Danach zeigt <http://127.0.0.1:8000> die installierten Tools. FeedVanta ist unter <http://127.0.0.1:8000/feedvanta/> erreichbar. Der eigenständige Host liegt im separaten Geschwisterprojekt `../pyhost`; für lokale Entwicklung muss es dort ausgecheckt sein. FeedVanta implementiert den Plugin-Vertrag strukturell und benötigt PyHost nicht als Projektabhängigkeit. Die SQLite-Datei wird standardmäßig als `data/feedvanta.db` angelegt. Mit `FEEDVANTA_DB=/anderer/pfad.db` lässt sich der Ort ändern.

### Frontend-Styles

Die HTML-Oberfläche verwendet Tailwind CSS. Node.js/npm werden für den CSS-Build benötigt. Die gepinnte CLI-Version steht in `package-lock.json`; `npm ci && npm run build:css` regeneriert `app/static/app.css`.

### Paket-Releases

Bei jedem neu gepushten Tag im Format `plugin-feedvanta-v<version>` führt `.github/workflows/plugin-release.yml` die Tests aus, prüft die Versionsübereinstimmung mit `pyproject.toml`, baut FeedVanta als Python-Wheel und Source-Archive und hängt beide als Assets an ein GitHub Release an. PyHost oder ein Deployment-Build kann das Wheel direkt aus einem Release installieren, zum Beispiel:

```bash
python -m pip install \
  https://github.com/viper3400/feedvanta/releases/download/plugin-feedvanta-v0.4.0/feedvanta-0.4.0-py3-none-any.whl
```

Das Release-Asset ist ein herunterladbares Paket, kein Python-Paketindex. Für private Repositories benötigt der Build, der es installiert, GitHub-Zugangsdaten.

### Betrieb hinter einem Reverse Proxy

Die Anwendung lädt beim Start automatisch eine `.env`-Datei. Kopiere die Vorlage und passe sie bei Bedarf an:

```bash
cp .env.example .env
```

FeedVanta ist ein Plugin des ASGI-Hosts und wird intern unter `/feedvanta/` eingebunden. Der Host unterstützt einen gemeinsamen externen Unterpfad über `X-Forwarded-Prefix`; der Proxy muss den Präfix aus der weitergeleiteten URL entfernen und im Header setzen. Alternativ kann `TOOL_HOST_BASE_PATH` in der `.env` gesetzt werden (zum Beispiel `TOOL_HOST_BASE_PATH=/suburl`). Ein vom Proxy gesendeter `X-Forwarded-Prefix` hat Vorrang. So können mehrere Plugins unter demselben externen Präfix ihre jeweiligen Routen behalten:

```bash
location /suburl/ {
    proxy_pass http://192.168.1.3:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /suburl;
}
```

Damit ist FeedVanta extern unter `https://example.com/suburl/feedvanta/` erreichbar; andere Plugins bleiben unter ihren jeweiligen Routen, etwa `/suburl/pydo/`. Setze diesen Header nur hinter einem vertrauenswürdigen Reverse Proxy. Ohne externen Präfix sind die direkten Host-Routen `/feedvanta/` und `/`.

## Anmelden mit Google einrichten

Die Feedverwaltung und ihre API sind durch Google-Anmeldung und die Berechtigung `Global Administration` geschützt. Der **erste Benutzer**, der sich erfolgreich anmeldet, erhält diese Berechtigung automatisch. Benutzer und Berechtigungen werden in SQLite gespeichert und bleiben nach einem Neustart erhalten. Globale Administratoren können die Berechtigung in der Verwaltung weiteren angemeldeten Benutzern geben oder entziehen; die letzte globale Administration kann nicht entfernt werden.

Die generierten RSS-Feeds unter `/feedvanta/feed/{id}.xml`, die Kontrollansicht unter `/feedvanta/reader/{id}` sowie der Host-Healthcheck `/health` bleiben bewusst ohne Anmeldung erreichbar.

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

- **App-Version** (`app_version`): Version des laufenden FeedVanta-Codes, derzeit `0.4.0`.
- **Schema-Version** (`schema_version`): Struktur des JSON-Konfigurationsformats. Ein Import ist nur bei unterstützter Schema-Version möglich.
- **Config-Version** (`config_version`): Revision der konkreten Feedkonfiguration. Sie wird bei jedem Hinzufügen oder Löschen eines Feeds und bei jeder Änderung der Filterregeln erhöht. Beim Import wird die Revision der importierten Konfiguration übernommen.

Globale Administratoren erreichen Import und Export über **Konfiguration** in der Weboberfläche. Alternativ stehen die geschützten Endpunkte `GET /api/config/export` und `POST /api/config/import` zur Verfügung. Ein Import ersetzt die bestehende Feedkonfiguration vollständig.

Das Exportformat enthält ausschließlich Feeds und Filterregeln:

```json
{
  "app_version": "0.4.0",
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

## Video-Downloads

Das eigenständige Skript `tools/feed_downloader.py` lädt alle Mediendateien aus einem bereits durch FeedVanta gefilterten RSS-/Atom-Feed. Es verwendet ausschließlich die Python-Standardbibliothek und benötigt weder Zugriff auf die FeedVanta-Datenbank noch eine Installation des Webservers.

Beispiel für Feed 4:

```bash
python tools/feed_downloader.py \
    http://127.0.0.1:8000/feedvanta/feed/4.xml \
  --output ./downloads \
  --workers 4
```

Der erste Parameter kann eine HTTP(S)-Feed-URL, eine lokale XML-Datei oder `-` für Standardeingabe sein. `--output` verwendet standardmäßig `./downloads`, `--workers` standardmäßig drei parallele Downloads. RSS-/Atom-Enclosures werden bevorzugt; für ältere FeedVanta-Ausgaben dienen direkte Links auf `.mp4`, `.m4v`, `.webm`, `.mkv` und `.mov` als Fallback.

Das Skript verarbeitet den Feed-Snapshot vollständig und beendet sich anschließend. Vorhandene Dateien werden bei jedem Lauf atomar ersetzt. Jeder fehlgeschlagene Download wird dreimal versucht; andere Downloads laufen parallel weiter. Ein neuer Feedstand erfordert einen erneuten Aufruf, beispielsweise über Cron oder systemd.

Exit-Codes:

- `0`: alle gefundenen Medien erfolgreich heruntergeladen
- `1`: mindestens ein Download ist nach drei Versuchen fehlgeschlagen
- `2`: Feed, Argumente oder Zielverzeichnis konnten nicht verarbeitet werden

HLS-/DASH-Streams, Videoplattform-Seiten, DRM und `yt-dlp` werden nicht unterstützt. Das Docker-Image enthält bewusst nur den FeedVanta-Webserver; das unabhängige Skript wird am gewünschten Download-Ziel ausgeführt.

## Filterlogik

- Eine passende `exclude`-Regel blendet einen Artikel aus.
- Gibt es aktive `include`-Regeln, muss mindestens eine davon passen.
- Ohne `include`-Regel bleiben alle nicht ausgeschlossenen Artikel sichtbar.
- Vergleiche sind ohne Beachtung der Groß-/Kleinschreibung; Regex nutzt Python-Syntax.
- Neue oder geänderte Regeln werden sofort auf bereits gespeicherte Artikel angewendet.

Feeds werden jede Minute auf ihre individuelle Aktualisierungsfrist geprüft. Zusätzlich gibt es in der Oberfläche eine manuelle Aktualisierung. HTTP-Fehler ändern den vorhandenen Artikelbestand nicht.

## API und RSS

- `GET /feedvanta/api/feeds` – Feeds samt Regeln auflisten
- `POST /feedvanta/api/feeds` – Feed als JSON anlegen
- `POST /feedvanta/api/feeds/{id}/rules` – Regel als JSON anlegen
- `POST /feedvanta/api/feeds/{id}/refresh` – Feed sofort abrufen
- `DELETE /feedvanta/api/feeds/{id}` – Feed löschen
- `DELETE /feedvanta/api/rules/{id}` – Regel löschen
- `GET /feedvanta/api/config/export` – versionierte Feedkonfiguration exportieren
- `POST /feedvanta/api/config/import` – versionierte Feedkonfiguration ersetzen
- `GET /feedvanta/feed/{id}.xml` – gefilterter RSS-Feed
- `GET /health` – Host-Healthcheck

Beispiel:

```bash
curl -X POST http://127.0.0.1:8000/feedvanta/api/feeds \
  -H 'content-type: application/json' \
  -d '{"name":"Heise", "source_url":"https://www.heise.de/rss/heise-atom.xml"}'
```
