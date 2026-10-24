# FeedVanta

FeedVanta lädt RSS-/Atom-Quellen regelmäßig, speichert Artikel lokal und veröffentlicht daraus mehrere unabhängig gefilterte Ausgabe-Feeds. Jede Quelle wird nur einmal geladen; jeder Ausgabe-Feed hat einen eigenen Namen, eigene Regeln sowie eigene RSS- und Lese-URLs. Die Ausgabe kann direkt in NetNewsWire abonniert werden.

In der Übersicht legt **Feed hinzufügen** eine neue Quelle mit ihrem ersten Ausgabe-Feed an. **Gefilterten Feed hinzufügen** erzeugt eine weitere Ausgabe aus einer bestehenden Quelle und wendet ihre Regeln sofort auf gespeicherte Artikel an. **Lesen** öffnet die gefilterte Browseransicht, **RSS** die abonnierbare XML-Ausgabe. Beide enthalten nur sichtbare Artikel mit Veröffentlichungsdatum innerhalb der letzten 24 Stunden.

Abrufintervall, automatisches Laden, Originaltitel, Icon und Abruffehler gehören zur gemeinsamen Quelle. Manuelles Laden aktualisiert alle ihre Ausgabe-Feeds. Das Löschen eines Ausgabe-Feeds entfernt nur dessen Regeln und Sichtbarkeitsergebnisse; Quelle, Artikel und andere Ausgaben bleiben erhalten. Das ausdrücklich bestätigte Löschen einer Quelle entfernt hingegen alle ihre Ausgaben und Artikel. Quellen ohne Ausgaben bleiben verwaltbar und werden bei aktiviertem automatischem Laden weiter abgerufen.

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
  https://github.com/viper3400/feedvanta/releases/download/plugin-feedvanta-v0.5.0/feedvanta-0.5.0-py3-none-any.whl
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

FeedVanta unterscheidet vier unabhängige Versionen:

- **App-Version** (`app_version`): Version des laufenden FeedVanta-Codes, derzeit `0.5.0`.
- **Schema-Version** (`schema_version`): Struktur des JSON-Konfigurationsformats, derzeit `2`. Schema `1` wird beim Import automatisch konvertiert; unbekannte Versionen werden abgelehnt.
- **Datenbankschema-Version** (`PRAGMA user_version`): Struktur der SQLite-Datenbank, derzeit `1`; wird unabhängig vom JSON-Format beim Start migriert.
- **Config-Version** (`config_version`): Revision der konkreten Konfiguration. Änderungen an Quellen, Ausgabe-Feeds oder Filterregeln erhöhen sie. Abrufe, Aufbewahrungsbereinigung und strukturelle Migrationen ändern sie nicht. Beim Import wird die Revision der importierten Konfiguration übernommen.

Globale Administratoren erreichen Import und Export über **Konfiguration** in der Weboberfläche. Alternativ stehen die geschützten Endpunkte `GET /api/config/export` und `POST /api/config/import` zur Verfügung. Ein Import ersetzt Quellen, Ausgabe-Feeds und Regeln vollständig und löscht heruntergeladene Artikel. Dabei können sich Abonnement-URLs ändern. Benutzer und Berechtigungen bleiben erhalten. Validierung und Konvertierung geschehen vor der atomaren Ersetzung; ungültige Importe ändern keine Daten.

Das Exportformat enthält ausschließlich Quellen, Ausgabe-Feeds und Filterregeln. Quellenschlüssel sind lokale Referenzen innerhalb der Datei, keine garantierten Datenbank- oder öffentlichen Feed-IDs:

```json
{
  "app_version": "0.5.0",
  "schema_version": 2,
  "config_version": 12,
  "exported_at": "2026-09-26T10:00:00+00:00",
  "sources": [
    {
      "key": "source-1",
      "source_url": "https://example.com/feed.xml",
      "enabled": true,
      "refresh_interval": 30
    }
  ],
  "feeds": [
    {
      "name": "Example ohne Sport",
      "source": "source-1",
      "rules": [
        {
          "field": "title",
          "operator": "contains",
          "value": "Sport",
          "action": "exclude",
          "enabled": true
        }
      ]
    },
    {"name": "Example komplett", "source": "source-1", "rules": []}
  ]
}
```

Benutzer, Berechtigungen, OAuth-Daten, Sitzungen, heruntergeladene Artikel sowie abgeleitete Feed-Metadaten werden niemals mitexportiert.

### Datenbank-Upgrade und Sicherung

Vor dem Upgrade die Anwendung stoppen und die SQLite-Datei sichern, oder eine konsistente Sicherung über die SQLite-Backup-Funktion erstellen. Keine laufende Datenbank durch einfaches Kopieren der Hauptdatei sichern, wenn noch Schreibzugriffe stattfinden.

Beim Start wird eine erkannte bisherige, unversionierte Datenbank transaktional auf das Quellen-/Ausgabe-Modell umgestellt. Pro bisherigem Feed entstehen eine Quelle und eine Ausgabe. Ausgabe-IDs, öffentliche RSS-/Lese-URLs, Regeln, Artikel samt Zeitstempeln und Sichtbarkeit, Quelleneinstellungen, Benutzer, Berechtigungen und Config-Revision bleiben erhalten. Der bisher sichtbare Originaltitel wird, falls vorhanden, zum Ausgabenamen; andernfalls bleibt der konfigurierte Name. Spätere Quellentiteländerungen überschreiben diesen Namen nicht.

Migrationen laufen vor Abrufen und normaler Aufbewahrungsbereinigung; sie löschen selbst keine Artikel. Zusätzliche kompatible Tabellen früherer Funktionen (etwa Downloads und Themen-Digests) samt Daten, Indizes und Artikelreferenzen bleiben erhalten. Fehlende Benutzer-, Berechtigungs- und Konfigurationstabellen älterer Installationen werden angelegt; eine bisher nicht vorhandene Config-Revision beginnt bei 1. Unbekannte Kernlayouts, Namenskonflikte, inkompatible Fremdschlüssel und neuere Schema-Versionen werden abgelehnt. Bei einem Fehler werden Änderungen zurückgerollt und der Start abgebrochen. Ein weiterer Start führt erfolgreiche Migrationen nicht erneut aus. Ein Downgrade erfordert das Wiederherstellen der Sicherung mit der dazu passenden alten Anwendung; eine Rückwärtsmigration wird nicht angeboten.

Die JSON-Konfiguration muss nicht beim Start separat migriert werden: Sie wird nur bei einem Import eingelesen. Historie und Entscheidungen dieser Änderung stehen in [Iteration 001](docs/iterations/001-multiple-output-feeds.md).

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

Quellen werden jede Minute auf ihre individuelle Aktualisierungsfrist geprüft, nicht einmal pro Ausgabe. Zusätzlich gibt es in der Oberfläche eine manuelle Aktualisierung. HTTP-Fehler ändern den vorhandenen Artikelbestand nicht. Filterregeln gelten ausschließlich für ihre Ausgabe, ohne Vererbung. Ein Ausgabe-Feed ohne aktive Regeln zeigt alle ansonsten zulässigen Artikel seiner Quelle.

## API und RSS

- `GET /feedvanta/api/feeds` – Feeds samt Regeln auflisten
- `POST /feedvanta/api/feeds` – Ausgabe mit `name` und `source_id` anlegen; alternativ mit `source_url` und optionalem `refresh_interval` eine neue Quelle samt erster Ausgabe erstellen
- `PATCH /feedvanta/api/feeds/{id}` – Ausgabenamen mit `name` ändern
- `POST /feedvanta/api/feeds/{id}/rules` – Regel als JSON anlegen
- `POST /feedvanta/api/feeds/{id}/refresh` – gemeinsame Quelle sofort abrufen und alle ihre Ausgaben aktualisieren
- `PATCH /feedvanta/api/feeds/{id}/refresh-interval` – gemeinsames Abrufintervall mit `refresh_interval` ändern
- `DELETE /feedvanta/api/feeds/{id}` – nur Ausgabe und ihre Regeln löschen; Quelle und Artikel behalten
- `GET /feedvanta/api/sources` – Quellen samt ihren Ausgaben auflisten
- `POST /feedvanta/api/sources` – neue Quelle samt erster Ausgabe mit `name`, `source_url` und optionalem `refresh_interval` erstellen; Antwort enthält `id` (Quelle) und `feed_id` (Ausgabe)
- `PATCH /feedvanta/api/sources/{id}` – `enabled` und/oder `refresh_interval` ändern
- `POST /feedvanta/api/sources/{id}/refresh` – Quelle sofort abrufen
- `DELETE /feedvanta/api/sources/{id}?confirm=true` – Quelle samt Ausgaben, Regeln und Artikeln löschen; bei vorhandenen Ausgaben ist `confirm=true` erforderlich
- `PATCH /feedvanta/api/rules/{id}` – Regel mit `field`, `operator`, `value` und
  `action` bearbeiten; ID und Aktivierungszustand bleiben erhalten, Filter werden
  für den betroffenen Ausgabe-Feed sofort neu angewendet
- `DELETE /feedvanta/api/rules/{id}` – Regel löschen
- `GET /feedvanta/api/config/export` – versionierte Feedkonfiguration exportieren
- `POST /feedvanta/api/config/import` – versionierte Feedkonfiguration ersetzen
- `GET /feedvanta/feed/{id}.xml` – gefilterter RSS-Feed
- `GET /health` – Host-Healthcheck

Beispiel:

Die Verwaltungs-API erfordert eine angemeldete Sitzung mit Global Administration; bei `curl` entsprechend ein Sitzungscookie mitsenden.

```bash
curl -X POST http://127.0.0.1:8000/feedvanta/api/feeds \
  -H 'content-type: application/json' \
  -d '{"name":"Heise", "source_url":"https://www.heise.de/rss/heise-atom.xml"}'
```

Weitere Ausgabe für eine vorhandene Quelle (Quellen-ID aus `GET /api/sources`):

```bash
curl -X POST http://127.0.0.1:8000/feedvanta/api/feeds \
  -H 'content-type: application/json' \
  -d '{"name":"Heise Python", "source_id":1}'
```

Anschließend Regeln über `/api/feeds/{id}/rules` hinzufügen. Eine bereits vorhandene `source_url` wird weiterhin mit HTTP 409 abgelehnt; dafür die vorhandene `source_id` verwenden. Bei referenzierten Quellen werden gemeinsame Einstellungen nicht im Ausgabe-Erstellungsrequest mitgegeben.
