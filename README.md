# GFSL-Bot

Ein Discord-Bot für die GFSL, aufgeteilt in Module. Jedes Modul übernimmt eine Aufgabe:

| Modul | Datei | Was es macht |
|---|---|---|
| **Ersatzfahrer** | `gfsl/cogs/ersatzfahrer.py` | Postet alle Mitglieder einer Rolle als Liste und hält sie automatisch aktuell. |
| **Verify** | `gfsl/cogs/verify/` | Verifizierung per Formular, Fahrernummern, automatische Nicknames, Fahrerliste mit Flaggen, Import der Melpo-Altdaten. |
| **Anmeldung** | `gfsl/cogs/events/` | Anmelde-Bot wie Raid-Helper: Rennkalender, automatisches Posten, Teams/Ersatzfahrer, Q-Sperre, Einteilung, Anwesenheit. |

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
      profile.py            /profil: verifizierte Fahrer tragen Flagge und Plattform selbst nach
      option_roles.py       Rollen je Antwort (z. B. Plattform-Rollen)
    events/                 Anmelde-Bot
      commands.py           Befehle /anmeldung, /vorlage, /kalender, /event, /anwesenheit + Zeitplaner (jede Minute)
      actions.py            Posten, An-/Abmelde-Buttons, Frist, Erinnerung, Schließen
      assign.py             Teams/Ersatzfahrer und zufällige Einteilung in freie Cockpits
      render.py             Darstellung wie Raid-Helper
      review.py             Freigabe per DM vor dem Posten, Bearbeiten-Dialog
      template_editor.py    /vorlage bearbeiten
      attendance.py         Anwesenheits-Statistik
      timeutil.py, data.py  deutsche Zeit (Sommer-/Winterzeit), gespeicherte Daten
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
   Für den Anmelde-Bot zusätzlich **„Erwähnen @everyone, @here und alle Rollen“**, damit er auch Rollen pingen kann, die nicht für alle erwähnbar sind.
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
4. **Fahrerliste:** Kanal für die automatische Liste. Dort stehen alle Nummern untereinander:
   ```
   11:                       ← frei
   13: 🇩🇪 @User (PC)
   ```
   Die Plattform kommt aus der Rolle des Fahrers (siehe „Rollen je Antwort“), sonst aus den gespeicherten Daten. Ändern sich die Rollen, aktualisiert sich die Liste nach ein paar Sekunden von selbst.
5. **Rollen:** Rolle bei Freigabe, Rolle, die entfernt wird (z. B. Unverifiziert), und **Admin-Rolle zum Bearbeiten mit dem Bot** (siehe „Berechtigungen“).
6. **Nickname & Nummern:** Vorlage wie `#{nummer} | {name}`, und Nummernbereich.
7. **Fragen:** Standardfragen laden und mit **Testlauf** selbst ausprobieren.
8. **Panel posten:** Postet den Start-Button (bzw. aktualisiert ihn) und baut die Fahrerliste auf.

Mit `/verify setup schritt:` springst du direkt zu einem Schritt. Zu fehlenden Rechten in einem Kanal und zu Rollen, die über der Bot-Rolle stehen, zeigt der Assistent eine Warnung.

## Berechtigungen

| Wer | Darf |
|---|---|
| **Admins** = alle mit „Server verwalten“ **oder** der Admin-Rolle aus `/verify setup` (Schritt 5) | alle Befehle, Anträge akzeptieren/ablehnen |
| **Verifizierte Fahrer** | nur `/profil` (eigene Flagge/Plattform) bzw. der Start-Button |
| **Alle anderen** | nur der Start-Button zum Verifizieren |

Der Bot prüft das bei jedem Befehl selbst. Fahrer sehen die Admin-Befehle gar nicht: Discord zeigt sie standardmäßig nur Leuten mit „Server verwalten“.
**Hat deine Admin-Rolle kein „Server verwalten“?** Dann unter *Servereinstellungen → Integrationen → GFSL-Bot* bei `/verify`, `/fragen`, `/fahrer` und `/ersatzfahrer` die Admin-Rolle hinzufügen, damit die Admins die Befehle sehen.

**Rollen beim Nachtragen:** Wählt jemand in `/profil` oder ein Admin in `/fahrer bearbeiten` eine Plattform, bekommt der Fahrer sofort die passende Rolle, und die Rolle der alten Plattform wird entfernt. Umgekehrt gilt auch: Wird eine Plattform-Rolle per Hand in Discord vergeben, übernimmt der Bot sie als Plattform und aktualisiert die Liste.

## Ablauf für den User

1. Klick auf **„Verifizierung starten“**. Es öffnet sich ein **Formular (Discord-Dialog)** mit bis zu 5 Fragen.
2. Danach erscheint unten ein **privates Overlay** („Nur du kannst das sehen“) mit dem Fortschritt und dem Button für den nächsten Teil.
3. Die **Fahrernummer** wählt man im Overlay aus allen freien Nummern (bei mehr als 25 erst einen Bereich, dann die Nummer). Nur schon vergebene Nummern tauchen nicht auf.
4. Am Ende kommt eine **Zusammenfassung**. Über „Etwas ändern …“ lässt sich jeder Teil noch einmal öffnen, danach auf **Abschicken** klicken.
5. Der Antrag landet im **Mod-Kanal**, mit Screenshots als Anhang und einer Vorschau der Nummer („Wunsch #17 belegt → bekommt #18“, falls die Nummer inzwischen weg ist).
6. **Akzeptieren:** Der Bot setzt den Nickname, vergibt bzw. entfernt die Rollen, trägt die Nummer ein, aktualisiert die Fahrerliste und schickt dem User eine DM.
   **Ablehnen:** Der Mod gibt optional einen Grund an, und der User bekommt ihn per DM.

**Schon verifiziert?** Ein Klick auf den Start-Button (oder `/profil`) öffnet einen Dialog, in dem man **Flagge und Plattform** nachtragen oder ändern kann. Die Plattform-Rolle wird dabei direkt angepasst.

Die Akzeptieren/Ablehnen-Buttons und der Start-Button funktionieren auch nach einem Neustart. Ein halb ausgefülltes Formular geht beim Neustart allerdings verloren, dann einfach neu starten.

## Befehle

Alle Befehle außer `/profil` sind nur für Admins (siehe „Berechtigungen“).

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
| `/fahrer bearbeiten fahrer:` | Fahrer aus der Liste wählen und Flagge, Plattform, Nummer, Name nachtragen. Rollen und Nickname werden direkt angepasst. |
| `/fahrer setzen` | Fahrer manuell eintragen oder Nummer ändern. Beim Tippen werden freie Nummern und Flaggen vorgeschlagen. |
| `/fahrer entfernen` | Fahrer austragen, die Nummer wird wieder frei. |
| `/fahrer rollen_sync` | Gibt allen eingetragenen Fahrern die Rollen passend zu ihrer Auswahl, auch nachträglich. |
| `/fahrer fehlende_angaben` | Zeigt, wem Flagge oder Plattform fehlt. Mit `oeffentlich:True` werden die Fahrer im Kanal angepingt, damit sie `/profil` nutzen. |
| `/profil` | Für Fahrer: Flagge und Plattform eintragen oder ändern. |
| `/fahrer nummern` | Übersicht aller Nummern (frei, vergeben), optional öffentlich. |
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

- Erkannt werden **Formular-Nachrichten** (Embed-Felder wie „Ingame-Name“, „Fahrernummer“, „Flagge“, „Plattform“, den User über die Erwähnung oder die User-ID) und **Listen-Zeilen** wie `13: @User` oder `#17 🇩🇪 Name @User` (z. B. eine alte Fahrerliste).
- Zeilen ohne User (z. B. `10: Pierre Gasly`, `17: //` oder leer) gelten als freie Nummern und werden ignoriert.
- Hat ein Mensch die alte Liste gepostet (nicht der Bot), braucht es `nur_bots:False`.
- Nachrichten mit „abgelehnt“/„denied“ werden übersprungen. Standardmäßig werden nur Nachrichten von Bots gelesen und nur User übernommen, die noch auf dem Server sind.
- Ist eine Nummer doppelt vergeben, behält sie der zuletzt gesehene Eintrag. Der andere wird ohne Nummer eingetragen und in der CSV markiert.
- Es kommt immer erst eine **Vorschau** mit CSV-Datei. Danach wählst du **Alle übernehmen**, **Nur neue übernehmen** oder **Abbrechen**.
- Stimmt etwas nicht, öffnest du die CSV in Excel, korrigierst sie und lädst sie mit `/fahrer import_csv` hoch.
- Stehen die Nummern schon in den Nicknames (`#17 | Name`), geht auch `/fahrer import_nicknames rolle:@Fahrer`.

Rollen und Nicknames werden beim Import **nicht** verändert. Die Plattform-Rollen vergibst du danach mit `/fahrer rollen_sync`. Erkennt der Bot nichts, schick ein Beispiel einer alten Melpo-Nachricht (Screenshot oder kopierter Text), dann lässt sich die Erkennung in `legacy_import.py` anpassen.

## Anmelde-Bot (Rennanmeldung wie Raid-Helper)

### 1. Einmal einrichten

```
/anmeldung klasse_neu rolle:@Ferrari art:Team emoji::ferrari: plaetze:2
/anmeldung klasse_neu rolle:@Haas art:Team emoji::haas:
...                                   (je Team eine)
/anmeldung klasse_neu rolle:@Ersatzfahrer art:Ersatzfahrer emoji::reserve:
/vorlage erstellen name:Liga kanal:#anmeldung
```

- **Klassen** sind die Rollen, mit denen man sich anmelden kann. Beim Anmelden schaut der Bot, welche Rolle der Fahrer hat, und sortiert ihn in die passende Spalte. Teams kommen vor Ersatzfahrern. Server-Emojis gehen als Icon (`:ferrari:` im Befehl tippen).
- In der **Vorlage** steht alles, was bei jedem Rennen gleich ist:
  - Kanal der Anmeldung, Kanal für Info + Erinnerung (leer = derselbe) und Rollen, die dort erwähnt werden
  - Beschreibung und Bild (`/vorlage bild`)
  - Uhrzeit des Rennens
  - wann gepostet wird, z. B. „3 Tage vorher um 10:00“
  - Anmeldefrist, z. B. „am Renntag um 15:00“
  - Freigabe per DM: ja/nein, wer die DM bekommt und wie viele Stunden vorher
  - 📢 Info beim Posten „Anmeldung ist offen“ (an/aus)
  - ⏰ Anmelde-Erinnerung, z. B. „1 18:00“ = am Tag vor dem Rennen um 18 Uhr (leer = aus)
- **Vorschau** im Vorlagen-Editor zeigt, wie die Anmeldung aussieht.

### 2. Season / Kalender

```
/kalender hinzufuegen vorlage:Liga datum:16.10.2026 titel:Abu Dhabi GP bild:<optional>
/kalender woechentlich vorlage:Liga erstes_datum:16.10.2026 anzahl:10     (Platzhalter „Rennen 1..10“)
/kalender liste
```

| Befehl | Was er macht |
|---|---|
| `/kalender bearbeiten` | Titel, Beschreibung, Datum und Bild ändern. Speichern zählt als Freigabe. |
| `/kalender verschieben eintrag: tage:7` | Verschiebt das Rennen. **Alle späteren Rennen der Vorlage rutschen mit** (`nachfolgende:False` = nur dieses). |
| `/kalender aussetzen` | Rennen wird nicht gepostet, die anderen bleiben, wo sie sind. |
| `/kalender freigeben` / `posten` | So freigeben bzw. sofort posten. |
| `/kalender entfernen` | Rennen löschen (eine gepostete Anmeldung wird mitgelöscht). |

### 3. Was automatisch passiert

1. **Freigabe** (wenn in der Vorlage an): Vor dem Posten bekommst du eine **DM mit Vorschau** und den Buttons *Bearbeiten & freigeben*, *So freigeben* und *Diese Woche aussetzen*. **Ohne Freigabe wird nichts gepostet.** Zum Post-Zeitpunkt kommt noch einmal eine Erinnerung. Sobald du freigibst, wird sofort gepostet.
2. **Posten** zur eingestellten Zeit im Kanal der Vorlage, mit den Buttons **Anmelden / Vielleicht / Abmelden** (die Anmeldung selbst pingt niemanden).
   Gleichzeitig kommt im Info-Kanal die Nachricht **„📢 Die Anmeldung für … ist offen!“** mit den eingestellten @-Rollen, Rennzeit, Frist und Link zur Anmeldung.
   Zur eingestellten Zeit folgt einmal die **„⏰ Erinnerung“**, ebenfalls mit @-Rollen, Link und der bisherigen Zahl der Anmeldungen. Liegt die Frist schon hinter uns, entfällt sie.
3. **Übersicht:** Teams als Spalten mit Icons und Plätzen (`Ferrari (2/2)`), eine eigene Spalte für Ersatzfahrer, darunter *Vielleicht* und *Abgemeldet*. Gezählt wird angemeldet / vielleicht / abgemeldet, und „vielleicht“ zählt nicht als angemeldet.
4. **Anmeldefrist:** Wer sich danach anmeldet, wird trotzdem eingetragen, bekommt aber eine **Q-Sperre**: Hinweis beim Klick, eine DM und ein ⏰ in der Übersicht.
5. **Einteilung:** Zur Frist werden die angemeldeten Ersatzfahrer auf die freien Cockpits verteilt (freie Plätze = Plätze minus angemeldete Stammfahrer).
   - **Wer zuerst angemeldet ist, bekommt zuerst einen Platz.** In welches Team er kommt, ist zufällig.
   - Wer keinen Platz bekommt, steht unten unter **„Keine Einteilung – alle Plätze belegt“** (in Anmelde-Reihenfolge) und rückt automatisch nach, wenn ein Platz frei wird.
   - **Stammfahrer, die sich zu spät anmelden**, bekommen die Q-Sperre **und verlieren ihren festen Platz**: Sie reihen sich wie Ersatzfahrer hinten ein und verdrängen keinen schon eingeteilten Fahrer. Ist noch etwas frei, fahren sie (bevorzugt im eigenen Team), sonst stehen sie unter „Keine Einteilung“.
   - Darunter erscheint eine Extra-Nachricht mit der Einteilung, „Keine Einteilung“ und den Q-Sperren.
   - Meldet sich danach noch jemand an oder ab, wird automatisch nachgetragen, und die Betroffenen werden angepingt.
   - Stammfahrer auf „vielleicht“ zählen als nicht da, ihr Cockpit wird also vergeben.
6. **Start des Rennens:** Die Anmeldung schließt, die Buttons verschwinden.

Verpasst der Bot einen Zeitpunkt (z. B. Neustart), holt er ihn beim nächsten Durchlauf nach. Alle Zeiten sind deutsche Zeit, Sommer- und Winterzeit inklusive.

### 4. Laufende Events und Statistik

| Befehl | Was er macht |
|---|---|
| **⚙️-Button an der Anmeldung** | Nur für Admins: Titel, Beschreibung, Start und Bild direkt am Event ändern. Wird der Start verschoben, berechnet der Bot Frist, Q-Sperren und Einteilung neu. |
| `/event teilnehmer eintrag: fahrer: status:` | Jemanden an-/abmelden oder austragen, z. B. wenn er per Nachricht Bescheid gegeben hat. |
| `/event neu_einteilen` | Ersatzfahrer komplett neu auslosen. |
| `/anwesenheit [vorlage] [oeffentlich]` | Alle Fahrer nach Anwesenheit sortiert (oben 100 %), mit ✅ angemeldet, ❔ vielleicht, ❌ abgemeldet, ➖ keine Rückmeldung, ⏰ zu spät. Darunter eine eigene Liste „Noch nie angemeldet“. |

Gezählt werden alle vergangenen Rennen und alle Mitglieder, die gerade eine Anmelde-Rolle haben. Wer erst später dazugekommen ist, hat dadurch einen niedrigeren Wert.

