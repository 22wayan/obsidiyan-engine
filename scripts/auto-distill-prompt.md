<!-- obsidiyan-automation: maschineller Lauf, gehoert nicht in den Corpus -->

Du destillierst unbeaufsichtigt die wartenden Kandidatenquellen dieses Repos.
Arbeite die Schritte genau in dieser Reihenfolge ab.

1. `.venv/bin/python -m obsidiyan distill --pending --limit 10` zeigt die offenen Quellen.
2. Fuer jede Quelle `.venv/bin/python -m obsidiyan show <doc_id>` lesen.
3. Pro Quelle entscheiden:
   - Sie enthaelt dauerhaftes, sessionuebergreifendes Wissen, also Entscheidungen
     mit Begruendung, stabile Praeferenzen, belastbare Projektstaende oder Fakten
     ueber Personen und Firmen. Dann Claims extrahieren.
   - Sonst als geprueft ohne Claims markieren, mit dem passenden Grund.
4. Claims sammeln in `/tmp/obsidiyan-claims.json`, Schema als JSON-Liste:
   `[{"entity": "Projekt- oder Personenname, leer wenn freistehend", "topic": "Titel der Note, in die der Claim gehoert", "key": "normalisierter Sachverhalt", "kind": "fakt|entscheidung|praeferenz|offen", "text": "die Aussage, ein Satz, ohne Fuellwoerter", "stated_on": "YYYY-MM-DD", "source_doc_id": "Pfad relativ zu corpus/", "confidence": "high|medium", "rationale": "Begruendung falls im Original genannt, sonst leer"}]`
   Danach `.venv/bin/python -m obsidiyan add-claims /tmp/obsidiyan-claims.json`.
   Bei leerer Liste diesen Schritt auslassen.
5. Quellen ohne Claims in `/tmp/obsidiyan-reviews.json`:
   `[{"doc_id": "...", "reason": "coursework|transient|third_party|repo_state|duplicate|personal_ephemera|sensitive|other", "reviewed_on": "heutiges Datum", "note": "kurze generische Begruendung, hoechstens 240 Zeichen"}]`
   Danach `.venv/bin/python -m obsidiyan add-reviews /tmp/obsidiyan-reviews.json`.
   Bei leerer Liste diesen Schritt auslassen.
6. `.venv/bin/python -m obsidiyan emit` schreibt die Notes aus dem Claim-Store
   und meldet am Ende, wie viele Claims nach `notes/` und wie viele nach
   `private/` gegangen sind.
   Meldet emit eine fehlende Entity-Nabe, lege KEINE neue Nabe an. Markiere die
   Quelle dann aber auch **nicht** als geprueft: nimm die betroffenen Claims
   wieder aus `/tmp/obsidiyan-claims.json` heraus, lass die Quelle offen und
   nenne die fehlende Nabe in deiner Zusammenfassung. Eine als geprueft
   markierte Quelle kommt nicht wieder; sie wegen einer fehlenden Nabe zu
   verbrennen waere der teuerste stille Fehler in diesem Ablauf.
7. `.venv/bin/python scripts/verify-graph.py` und
   `.venv/bin/python scripts/verify-nda.py` ausfuehren. Beide muessen gruen sein.

Feste Regeln:

- `confidence` ist "high" nur bei einem woertlichen Nutzer-Turn, sonst "medium".
- Gleicher Sachverhalt heisst gleicher `key`, damit die neuere Aussage die
  aeltere ueberholt statt sie zu duplizieren.
- Niemals Kontaktdaten, Wohnadressen, Telefonnummern, Account-IDs oder Secrets
  in einen Claim schreiben, auch nicht wenn sie in der Quelle stehen.
- **Vertrauliche und kundenbezogene Quellen werden destilliert wie jede andere.**
  Seit dem 24.08.2026 landet ihr Destillat automatisch in `private/` statt in
  `notes/`; `emit` liest die Stufe aus dem Corpus und entscheidet selbst. Du
  setzt dafuer nichts und laesst solche Quellen nicht mehr mit reason
  "sensitive" liegen. Ein vertraulicher Projektstand, der nur als Rohtranskript
  im Corpus liegt, ist genau das, was dieses Brain verhindern soll.
- **Naben im privaten Layer brauchen einen privaten Parent.** Der Layer ist nach
  Mandat in Ordner sortiert; `ls private/*/` zeigt die vorhandenen. Waehle die
  fachlich passende bestehende Note und uebergib sie als
  `--entity-parent "<Entity>=private/<ordner>/<note>"`. Die neue Nabe landet
  dann automatisch in demselben Ordner. Findest du keine passende Note, lege
  keine an: Claims zuruecknehmen, Quelle offen lassen, in der Zusammenfassung
  nennen welche Nabe fehlt.
- **Ein Thema mischt keine Stufen.** Claims aus einer vertraulichen und aus
  einer offenen Quelle duerfen nicht dasselbe `topic` tragen, eine Note kann
  nicht halb privat sein. Fuer den vertraulichen Teil ein eigenes Thema waehlen,
  sonst lehnt `emit` mit "mischt clean und nda" ab.
- reason "sensitive" bleibt nur fuer den Fall, dass die Quelle ausser Secrets
  oder Kontaktdaten nichts Dauerhaftes enthaelt.
- `notes/` und `private/` niemals von Hand bearbeiten. Notes entstehen
  ausschliesslich ueber emit.
- Keine git-Befehle, keine Branch-Wechsel, keine Commits. Das erledigt das
  aufrufende Skript.
- Im Zweifel lieber als geprueft ohne Claims markieren als einen unsicheren
  Claim schreiben. Ein fehlender Claim ist billig, ein falscher vergiftet die Note.
- Zum Schluss in einem Satz zusammenfassen, was du getan hast.