#!/usr/bin/env bash
# daily.sh — taeglicher Brain-Lauf, gedacht fuer launchd.
#
# Zweck: den Corpus ohne Zutun aktuell halten und danach genau eine Frage
# beantworten, naemlich ob etwas auf den Nutzer wartet. Der Job destilliert
# absichtlich nicht selbst: Notes entstehen nur mit Freigabe, das ist die
# Kernregel aus notes/knowledge.md.
#
# Ergaenzt den SessionEnd-Hook, ersetzt ihn nicht. Der Hook deckt "nach dem
# Arbeiten" ab, dieser Job deckt lange laufende und liegengebliebene Sessions
# ab, deren Corpus-Dokument sonst tagelang veraltet.
set -uo pipefail
cd "$(dirname "$0")/.."

LOG="$HOME/.claude/obsidiyan-daily.log"
PY=".venv/bin/python"
STAMP="$(date '+%Y-%m-%d %H:%M')"

{
  echo "=================================================="
  echo "Brain-Tageslauf $STAMP"
  echo "=================================================="
} > "$LOG"

# 0. Lokalen main mit origin gleichziehen. Ohne das zeigt Obsidian weiter den
#    Stand von vor dem letzten Merge: der Vault ist der Arbeitsbaum, und ein
#    gemergter PR aendert zunaechst nur origin. Bewusst nur fast-forward und nur
#    auf main, damit unbeaufsichtigt kein Merge-Konflikt entsteht.
if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "main" ]; then
  git fetch origin main --quiet 2>/dev/null
  if git merge --ff-only origin/main --quiet 2>/dev/null; then
    echo "--- main auf origin gleichgezogen ---" >> "$LOG"
  else
    echo "--- main nicht fast-forward-bar, Vault bleibt auf lokalem Stand ---" >> "$LOG"
  fi
else
  echo "--- nicht auf main, kein Pull ---" >> "$LOG"
fi

# 1. Corpus aktualisieren. refresh.sh laeuft mit set -e und bricht bei rotem
#    Gate ab; das darf den Tageslauf nicht beenden, der Status wird unten
#    ohnehin einzeln erhoben und berichtet.
echo "--- Refresh ---" >> "$LOG"
bash scripts/refresh.sh >> "$LOG" 2>&1
REFRESH_RC=$?

# 2. Gates einzeln pruefen, damit der Bericht sagt welches klemmt.
#    verify-nda druckt die Urteilszeile je nach Ausgang vor oder nach der
#    Bestandszeile, deshalb gezielt darauf greppen statt tail -1.
GRAPH="$("$PY" scripts/verify-graph.py 2>&1 | tail -1)"
NDA="$("$PY" scripts/verify-nda.py 2>&1 | grep -E '^(PASS|FAIL)' | tail -1)"
NDA="${NDA:-FAIL: Urteilszeile nicht gefunden}"

# 3. Was wartet auf Freigabe.
PENDING_RAW="$("$PY" -m obsidiyan distill --pending --limit 10 2>/dev/null)"
PENDING_N="$(printf '%s' "$PENDING_RAW" | head -1 | grep -oE '^[0-9]+' || echo 0)"
STATS="$("$PY" -m obsidiyan stats 2>/dev/null | head -1)"

{
  echo
  echo "--- Bericht ---"
  echo "Refresh-Exitcode : $REFRESH_RC"
  echo "Bestand          : $STATS"
  echo "Graph            : $GRAPH"
  echo "NDA              : $NDA"
  echo "Offene Kandidaten: $PENDING_N"
  echo
  echo "$PENDING_RAW"
} >> "$LOG"

# 4. Destillation anstossen, wenn Kandidaten warten und beide Gates gruen sind.
#    Bei rotem Gate wird nicht destilliert: erst die Struktur reparieren, dann
#    neues Wissen darauf schreiben.
DESTILLAT=""
if [ "${PENDING_N:-0}" -gt 0 ] && [ "${GRAPH:0:10}" = "graph=PASS" ] && [ "${NDA:0:4}" = "PASS" ]; then
  {
    echo
    echo "--- Destillation ---"
  } >> "$LOG"
  DESTILLAT="$(bash scripts/auto-distill.sh 2>&1 | tee -a "$LOG" | tail -1)"
fi

# 5. Nur melden, wenn wirklich etwas ansteht. Ein stiller Tag ist ein guter Tag.
NOTE=""
case "$NDA" in
  PASS*) ;;
  *) NOTE="NDA-Gate rot" ;;
esac
case "$GRAPH" in
  graph=PASS*) ;;
  *) NOTE="${NOTE:+$NOTE, }Graph-Gate rot" ;;
esac
# Erfolg laeuft still. Gerufen wird nur, was ein Mensch entscheiden muss:
# eine Pruefung, die nach drei Reparaturrunden nicht sauber wurde, oder ein
# technischer Abbruch. Ein uebernommenes Destillat steht im Log, nicht als
# Benachrichtigung.
case "$DESTILLAT" in
  *uebernommen*)   : ;;   # deckt auch "privat uebernommen" ab
  *"wartet noch"*) NOTE="${NOTE:+$NOTE, }$DESTILLAT" ;;
  *PRUEFUNG*)      NOTE="${NOTE:+$NOTE, }$DESTILLAT" ;;
  *ABBRUCH*|*FEHLER*|*fehlgeschlagen*) NOTE="${NOTE:+$NOTE, }Destillation abgebrochen, siehe Log" ;;
  # Der Agent lief, hat aber nichts geschrieben. Frueher lief das still, und
  # wartende Kandidaten blieben unbemerkt liegen.
  *"kein PR noetig"*) NOTE="${NOTE:+$NOTE, }$PENDING_N Quellen warten, Destillation ergab keine Aenderung" ;;
  *) [ "${PENDING_N:-0}" -gt 0 ] && [ -z "$DESTILLAT" ] && NOTE="${NOTE:+$NOTE, }$PENDING_N Quellen warten, Destillation lief nicht" ;;
esac

if [ -n "$NOTE" ]; then
  echo "MELDUNG: $NOTE" >> "$LOG"
  osascript -e "display notification \"$NOTE\" with title \"Obsidiyan\" subtitle \"Tageslauf $STAMP\"" 2>/dev/null || true
else
  echo "MELDUNG: nichts offen" >> "$LOG"
fi

exit 0
