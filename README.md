# GFSL-Bot

Ein Discord-Bot für die GFSL, aufgeteilt in Module. Jedes Modul übernimmt eine Aufgabe:

| Modul | Datei | Was es macht |
|---|---|---|
| **Ersatzfahrer** | `gfsl/cogs/ersatzfahrer.py` | Postet alle Mitglieder einer Rolle als Liste und hält sie automatisch aktuell. |
| **Verify** | `gfsl/cogs/verify/` | Verifizierung per Formular, Fahrernummern, automatische Nicknames, Fahrerliste mit Flaggen, Import der Melpo-Altdaten. |

## Aufbau

```
main.py                     Startpunkt (python main.py)
gfsl/
  bot.py                    Bot, Intents, Liste der Module (EXTENSIONS)
  storage.py                JSON-Datenbank (data/db.json), jedes Modul hat pro Server einen eigenen Bereich
  cogs/
    ersatzfahrer.py         Ersatzfahrer-Liste
    verify/
      __init__.py           meldet das Modul beim Bot an
      commands.py           Slash-Befehle /verify, /fragen, /fahrer
      setup_wizard.py       /verify setup: Einrichtung Schritt für Schritt
      flow.py               Ablauf für den User: Start-Button → Formular → Overlay → Abschicken
      review.py             Mod-Kanal: Antrag mit Akzeptieren/Ablehnen
      drivers.py            Fahrernummern, Nicknames, Fahrerliste
      questions.py          Fragetypen, Prüfung, Standardfragen
      legacy_import.py      Altdaten aus dem Melpo-Kanal, aus Nicknames oder aus CSV
      flags.py, constants.py, data.py
```

**Neues Modul hinzufügen:** Datei in `gfsl/cogs/` anlegen (mit `async def setup(bot)`, siehe `ersatzfahrer.py`) und in `gfsl/bot.py` bei `EXTENSIONS` eintragen.

## Installation

1. **Developer Portal** (https://discord.com/developers/applications) → deine App → *Bot*:
   **Server Members Intent** und **Message Content Intent** einschalten. Den Message Content Intent braucht der Import, um die Nachrichten des Melpo-Bots lesen zu können.
2. **Einladen:** *OAuth2 → URL Generator* → Scopes `bot` und `applications.commands`.
   Rechte: Kanäle ansehen, Nachrichten senden, Links einbetten, Dateien anhängen, Nachrichtenverlauf lesen, Rollen verwalten, Nicknames verwalten.
3. **Rollenreihenfolge:** In den Servereinstellungen die Bot-Rolle **über** die Rollen der Fahrer ziehen, sonst kann der Bot weder Nicknames setzen noch Rollen vergeben. Den Server-Owner kann grundsätzlich kein Bot umbenennen.
4. **Starten** (Python 3.10 oder neuer):
   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env      # Token eintragen
   .venv/bin/python main.py
   ```
   Beim Hoster reicht `python main.py` mit der Umgebungsvariable `DISCORD_TOKEN`.
   Alle Daten liegen in `data/db.json`. Bei Hostern ohne festen Speicher mit `DATA_FILE` einen dauerhaften Pfad (Volume) angeben, sonst sind die Daten nach jedem Redeploy weg. Sichere die Datei ab und zu.

> Umstieg vom alten Bot: `/setchannel` und `/setrole` heißen jetzt `/ersatzfahrer kanal` und `/ersatzfahrer rolle`. Eine vorhandene `config.json` wird beim ersten Start automatisch übernommen.

## Verify einrichten

```
/verify setup
```

Der Assistent führt in einer privaten Nachricht durch acht Schritte:

1. **Start-Kanal:** Hier steht der Button „Verifizierung starten“.
2. **Start-Nachricht:** Titel, Text und Button-Beschriftung festlegen, mit Vorschau.
3. **Mod-Kanal:** Hier landen die ausgefüllten Anträge.
4. **Fahrerliste:** Kanal für die automatische Liste (`#17 🇩🇪 Name @User`).
5. **Rollen:** Rolle bei Freigabe, Rolle, die entfernt wird (z. B. Unverifiziert), und Mod-Rolle.
6. **Nickname & Nummern:** Vorlage wie `#{nummer} | {name}`, Nummernbereich und reservierte Nummern.
7. **Fragen:** Standardfragen laden und mit **Testlauf** selbst ausprobieren.
8. **Panel posten:** Postet den Start-Button (bzw. aktualisiert ihn) und baut die Fahrerliste auf.

Mit `/verify setup schritt:` springst du direkt zu einem Schritt. Zu fehlenden Rechten in einem Kanal und zu Rollen, die über der Bot-Rolle stehen, zeigt der Assistent eine Warnung.

## Ablauf für den User

1. Klick auf **„Verifizierung starten“**. Es öffnet sich ein **Formular (Discord-Dialog)** mit bis zu 5 Fragen.
2. Danach erscheint unten ein **privates Overlay** („Nur du kannst das sehen“) mit dem Fortschritt und dem Button für den nächsten Teil.
3. Die **Fahrernummer** wählt man im Overlay aus allen freien Nummern (bei mehr als 25 erst einen Bereich, dann die Nummer). Vergebene und reservierte Nummern tauchen nicht auf.
4. Am Ende kommt eine **Zusammenfassung**. Über „Etwas ändern …“ lässt sich jeder Teil noch einmal öffnen, danach auf **Abschicken** klicken.
5. Der Antrag landet im **Mod-Kanal**, mit Screenshots als Anhang und einer Vorschau der Nummer („Wunsch #17 belegt → bekommt #18“, falls die Nummer inzwischen weg ist).
6. **Akzeptieren:** Der Bot setzt den Nickname, vergibt bzw. entfernt die Rollen, trägt die Nummer ein, aktualisiert die Fahrerliste und schickt dem User eine DM.
   **Ablehnen:** Der Mod gibt optional einen Grund an, und der User bekommt ihn per DM.

Die Akzeptieren/Ablehnen-Buttons und der Start-Button funktionieren auch nach einem Neustart. Ein halb ausgefülltes Formular geht beim Neustart allerdings verloren, dann einfach neu starten.

## Befehle

Alle Befehle sind nur für Leute mit „Server verwalten“ sichtbar. Das kannst du unter *Servereinstellungen → Integrationen* anpassen.

| Befehl | Was er macht |
|---|---|
| `/verify setup` | Einrichtungs-Assistent (siehe oben). |
| `/verify einstellungen` | Zeigt alle Einstellungen mit Warnungen. |
| `/verify panel` | Postet den Start-Button neu bzw. aktualisiert ihn. |
| `/fragen liste` | Alle Fragen, aufgeteilt in die Formular-Teile. |
| `/fragen vorschau` | Testlauf: die Verifizierung selbst durchgehen, es wird nichts gespeichert. |
| `/fragen neu` | Neue Frage: `typ`, `frage` (max. 45 Zeichen), optional `hinweis` (max. 100), `pflicht`, `optionen`, `min`, `max`, `feld`, `position`. |
| `/fragen bearbeiten nr:` | Einzelne Teile ändern (`hinweis:-` bzw. `optionen:-` leert das Feld, `min:-1`/`max:-1` entfernt die Grenze). |
| `/fragen rollen nr:` | Rollen je Antwort festlegen, z. B. PlayStation → @PS-Fahrer (siehe unten). |
| `/fragen verschieben` / `entfernen` | Reihenfolge ändern bzw. Frage löschen. |
| `/fragen standard` | Lädt die GFSL-Startfragen (ersetzt alle aktuellen). |
| `/fahrer setzen` | Fahrer manuell eintragen oder Nummer ändern. Beim Tippen werden freie Nummern und Flaggen vorgeschlagen. |
| `/fahrer entfernen` | Fahrer austragen, die Nummer wird wieder frei. |
| `/fahrer rollen_sync` | Gibt allen eingetragenen Fahrern die Rollen passend zu ihrer Auswahl, auch nachträglich. |
| `/fahrer nummern` | Übersicht aller Nummern (frei, vergeben, reserviert), optional öffentlich. |
| `/fahrer aktualisieren` | Fahrerliste neu aufbauen. |
| `/fahrer export` | Alle Fahrer als CSV (Excel). |
| `/fahrer import_kanal` / `import_nicknames` / `import_csv` | Altdaten übernehmen (siehe unten). |
| `/ersatzfahrer kanal` / `rolle` | Ersatzfahrer-Liste einrichten. |

### Fragetypen

Discord erlaubt pro Formular 5 Felder. Die Überschrift einer Frage darf höchstens 45 Zeichen lang sein, längere Erklärungen gehören in `hinweis` (bis 100 Zeichen).

| Typ | Im Formular |
|---|---|
| Freitext (kurz/lang) | Textfeld |
| Zahl | Textfeld, wird geprüft (`min`/`max`) |
| Rundenzeit | Textfeld, `1.05.013` wird zu `1:05.013` |
| Auswahl | Dropdown (max. 25 Optionen) |
| Flagge | Dropdown mit Flaggen (ohne eigene Optionen: 25 Standardländer) |
| Ja/Nein | Radio-Buttons |
| Haken / Bestätigen | Checkbox (bei Pflicht muss sie angehakt sein) |
| Screenshot | Datei-Upload direkt im Formular (bis zu 4 Bilder) |

### Feld (automatische Aktion)

- **Ingame-Name → Nickname**: wird über die Nickname-Vorlage gesetzt (max. 32 Zeichen).
- **Fahrernummer**: bekommt eine eigene Auswahl-Seite mit allen freien Nummern. Ist die Nummer beim Akzeptieren schon weg, nimmt der Bot die nächste freie.
- **Flagge**: steht in der Fahrerliste vor dem Namen und ist als `{flagge}` im Nickname verfügbar.
- **Plattform**: wird gespeichert und ist als `{plattform}` im Nickname verfügbar.

### Rollen je Antwort (z. B. Plattform)

Bei Fragen vom Typ Auswahl, Flagge oder Ja/Nein kann jede Option eine Rolle bekommen:

```
/fragen rollen nr:1        → Option wählen (z. B. PlayStation) → Rolle wählen (z. B. @PS-Fahrer)
```

- Beim **Akzeptieren** bekommt der Fahrer die Rolle zu seiner Auswahl.
- **Bestehende Fahrer:** `/fahrer rollen_sync` oder der Button „Auf alle Fahrer anwenden“ vergibt fehlende Rollen. Die Auswahl kommt aus den Fahrerdaten (Plattform/Flagge aus Import, CSV oder `/fahrer setzen`), sonst aus der letzten Verifizierung.
- Hat ein Fahrer keine gespeicherte Plattform, aber schon genau eine der Plattform-Rollen, übernimmt der Bot die Plattform von dort.
- Fahrer ganz ohne Angabe werden aufgelistet. Nachtragen geht mit `/fahrer setzen plattform:`, das passt die Rolle sofort an.
- `andere_entfernen:True` nimmt Rollen anderer Optionen weg, z. B. die PS-Rolle nach einem Wechsel auf PC.
- Schreibweisen wie „PS5“ oder „ps4“ werden „PlayStation“ zugeordnet.

## Altdaten vom Melpo-Bot übernehmen

Der Bot liest die alten Nachrichten im Kanal vom ältesten bis zum neuesten Eintrag. Spätere Nachrichten überschreiben frühere, so landet der **aktuelle Stand** in der Datenbank.

```
/fahrer import_kanal kanal:#alter-verify-kanal
```

- Erkannt werden **Formular-Nachrichten** (Embed-Felder wie „Ingame-Name“, „Fahrernummer“, „Flagge“, „Plattform“, den User über die Erwähnung oder die User-ID) und **Listen-Zeilen** wie `#17 🇩🇪 Name @User` (z. B. eine alte Fahrerliste).
- Nachrichten mit „abgelehnt“/„denied“ werden übersprungen. Standardmäßig werden nur Nachrichten von Bots gelesen und nur User übernommen, die noch auf dem Server sind.
- Ist eine Nummer doppelt vergeben, behält sie der zuletzt gesehene Eintrag. Der andere wird ohne Nummer eingetragen und in der CSV markiert.
- Es kommt immer erst eine **Vorschau** mit CSV-Datei. Danach wählst du **Alle übernehmen**, **Nur neue übernehmen** oder **Abbrechen**.
- Stimmt etwas nicht, öffnest du die CSV in Excel, korrigierst sie und lädst sie mit `/fahrer import_csv` hoch.
- Stehen die Nummern schon in den Nicknames (`#17 | Name`), geht auch `/fahrer import_nicknames rolle:@Fahrer`.

Rollen und Nicknames werden beim Import **nicht** verändert. Die Plattform-Rollen vergibst du danach mit `/fahrer rollen_sync`. Erkennt der Bot nichts, schick ein Beispiel einer alten Melpo-Nachricht (Screenshot oder kopierter Text), dann lässt sich die Erkennung in `legacy_import.py` anpassen.
