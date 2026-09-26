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
- `GET /feed/{id}.xml` – gefilterter RSS-Feed
- `GET /health` – Healthcheck

Beispiel:

```bash
curl -X POST http://127.0.0.1:8000/api/feeds \
  -H 'content-type: application/json' \
  -d '{"name":"Heise", "source_url":"https://www.heise.de/rss/heise-atom.xml"}'
```
