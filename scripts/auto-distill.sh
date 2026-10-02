#!/usr/bin/env bash
# auto-distill.sh — destilliert wartende Kandidaten unbeaufsichtigt und legt das
# Ergebnis als Pull Request vor. Wird von daily.sh aufgerufen.
#
# Grundsatz: der Lauf schreibt nie auf main. Er arbeitet auf einem Bot-Branch,
# beide Gates muessen gruen sein, und erst dann entsteht ein PR. Die Freigabe
# bleibt ein Merge-Klick, das ist die Stelle, an der ein Mensch draufschaut.
#
# Der eigentliche Destillationsschritt laeuft als headless Claude-Code-Sitzung
# mit einer engen Werkzeugliste: lesen, schreiben, und ausschliesslich die
# obsidiyan-CLI. Kein git in der Hand des Agenten, das macht dieses Skript.
set -uo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"

# Denselben Lock nehmen wie refresh.sh. Ohne ihn laufen Destillation und
# Corpus-Refresh gleichzeitig, und der Refresh zieht dem Lauf die Quellen unter
# den Fuessen weg: die Claim-Stores zeigen dann auf doc_ids, die es in diesem
# Moment nicht gibt. Beobachtet am 2026-08-28, waehrend der Kurs-Ingest den
# Corpus von 3.790 auf 9.713 Dokumente brachte: verify-nda meldete 50 Lecks vom
# Typ "Corpus-Quelle fehlt", verify-graph 117 Knoten ohne Parent. Direkt danach
# waren beide Gates wieder gruen. Der Lauf hatte nichts kaputt gemacht, er hatte
# nur in einen halbfertigen Corpus geschaut und deshalb abgebrochen.
#
# Ueberspringen statt warten ist hier richtig, wie beim Refresh selbst: der Lauf
# ist idempotent, der naechste holt alles Fehlende nach.
LOCK="${TMPDIR:-/tmp}/obsidiyan-refresh.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -30 2>/dev/null)" ]; then
    printf 'verwaister Lock aelter als 30 Minuten, wird uebernommen\n'
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { printf 'Lock nicht erlangbar, uebersprungen\n'; exit 0; }
  else
    printf 'Refresh laeuft gerade, Destillation wird uebersprungen\n'
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

BRANCH="brain/destillat-$(date '+%Y-%m-%d')"
TODAY="$(date '+%Y-%m-%d')"

# launchd startet diesen Lauf mit dem minimalen Default-PATH
# (/usr/bin:/bin:/usr/sbin:/sbin). Die Claude-CLI liegt dort nicht. Ohne
# explizite Aufloesung scheiterte der Destillationsschritt mit
# "claude: command not found", das Skript lief weiter und meldete
# "keine Aenderung an distill/ oder notes/". Im Tagesbericht sah ein nie
# gelaufener Agent damit exakt aus wie "nichts zu tun".
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}"
for candidate in "$HOME/.local/bin/claude" "/opt/homebrew/bin/claude" "/usr/local/bin/claude"; do
  [ -n "$CLAUDE_BIN" ] && break
  [ -x "$candidate" ] && CLAUDE_BIN="$candidate"
done

# Prompt-Datei einlesen, ohne YAML-Frontmatter.
#
# Die Prompt-Dateien sind Teil des Obsidian-Vaults und koennen deshalb ein
# Frontmatter mit parent-Property bekommen. Beginnt der Prompt damit, faengt er
# mit "---" an, und "claude -p" liest das als Option:
#
#   error: unknown option '---
#
# Der Lauf brach dadurch sofort ab, ohne dass im Log ein Grund stand: die
# Fehlermeldung enthaelt den kompletten Prompt, und "tail -20" zeigte davon nur
# das Ende. Zwei Naechte lang sah das aus wie ein Modellfehler.
prompt_ohne_frontmatter() {
  awk 'NR==1 && $0=="---" {in_fm=1; next} in_fm && $0=="---" {in_fm=0; next} !in_fm' "$1"
}

say() { echo "$*"; }

# Wie viele Claims liegen im gitignorierten Store? Ein rein privater Lauf
# aendert keinen getrackten Pfad; ohne diesen Zaehler meldet der Lauf "keine
# Aenderung", obwohl er gearbeitet hat.
priv_claims() {
  "$PY" - <<'PYCOUNT' 2>/dev/null || echo 0
import json, pathlib
p = pathlib.Path("distill/claims-private.json")
print(len(json.loads(p.read_text(encoding="utf-8"))) if p.is_file() else 0)
PYCOUNT
}

# Bricht den Lauf ab und raeumt den Bot-Branch weg, solange er nichts enthaelt.
# Ein Branch mit Arbeit darauf bleibt stehen, wie im Gate-rot-Fall.
abort() {
  if [ -n "${START_BRANCH:-}" ]; then
    if [ "$(git status --porcelain -- distill notes | wc -l | tr -d ' ')" -eq 0 ]; then
      git checkout -q "$START_BRANCH" 2>/dev/null
      git branch -q -D "$BRANCH" 2>/dev/null
    else
      git add distill notes 2>/dev/null
      git commit -q -m "wip: Destillat $TODAY, Lauf abgebrochen" 2>/dev/null
      git checkout -q "$START_BRANCH" 2>/dev/null
      say "Branch $BRANCH bleibt zur Ansicht stehen."
    fi
  fi
  # Die Meldung kommt zuletzt: daily.sh wertet die letzte Zeile aus.
  say "FEHLER: $*"
  exit 1
}

# 0. Laeuft ueberhaupt etwas an? -------------------------------------------
PRIV_BEFORE="$(priv_claims)"
# Schnappschuss des gitignorierten Stores, damit die Pruefinstanz spaeter
# erkennt, welche privaten Claims dieser Lauf neu behauptet hat.
PRIV_SNAPSHOT="${TMPDIR:-/tmp}/obsidiyan-claims-private-before.json"
if [ -f distill/claims-private.json ] && [ "${OBSIDIYAN_VERIFY_ALL:-0}" != "1" ]; then
  cp distill/claims-private.json "$PRIV_SNAPSHOT"
else
  # Leerer Schnappschuss heisst: der gesamte private Store gilt als neu und geht
  # durch die Gegenpruefung. Gebraucht, wenn ein Lauf abgebrochen ist und Claims
  # zurueckgelassen hat, die nie geprueft wurden.
  printf '[]' > "$PRIV_SNAPSHOT"
fi
PENDING_RAW="$("$PY" -m obsidiyan distill --pending --limit 10 2>/dev/null)"
PENDING_N="$(printf '%s' "$PENDING_RAW" | head -1 | grep -oE '^[0-9]+' || echo 0)"
if [ "${PENDING_N:-0}" -eq 0 ]; then
  say "nichts offen"
  exit 0
fi

# 0b. Werkzeug pruefen, bevor irgendetwas angelegt wird. Ein fehlender Agent
#     ist ein Abbruch mit Meldung, nicht ein stiller Lauf ohne Ergebnis.
if [ -z "$CLAUDE_BIN" ]; then
  say "FEHLER: claude-CLI nicht gefunden. PATH=$PATH"
  exit 1
fi

# 1. Liegt schon ein Destillat-PR? Dann heute nicht nachlegen, sonst stapeln
#    sich Branches mit konkurrierenden Claim-Store-Staenden.
OPEN_PR="$(gh pr list --state open --json number,headRefName \
  --jq '[.[] | select(.headRefName | startswith("brain/destillat"))] | .[0].number' 2>/dev/null)"
if [ -n "$OPEN_PR" ] && [ "$OPEN_PR" != "null" ]; then
  say "PR #$OPEN_PR wartet noch auf Freigabe, kein neuer Lauf"
  exit 0
fi

# 2. Bot-Branch aus dem aktuellen main. Fremde unversionierte Aenderungen
#    (etwa Memory-Snapshots aus einer parallelen Sitzung) bleiben unangetastet,
#    weil spaeter nur distill/ und notes/ gestaged werden.
git fetch origin main --quiet 2>/dev/null
START_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git checkout -q -B "$BRANCH" origin/main 2>/dev/null || {
  say "FEHLER: Branch $BRANCH nicht anlegbar"
  exit 1
}

# 3. Destillation. Enge Werkzeugliste, der Agent bekommt kein git.
PROMPT="$(prompt_ohne_frontmatter scripts/auto-distill-prompt.md)"

say "--- Destillation laeuft ($PENDING_N Kandidaten) ---"
"$CLAUDE_BIN" -p "$PROMPT" \
  --allowedTools "Read" "Write" "Bash(.venv/bin/python:*)" \
  --permission-mode acceptEdits \
  2>&1 | tail -20
AGENT_RC=${PIPESTATUS[0]}
[ "$AGENT_RC" -eq 0 ] || abort "Destillationslauf beendete mit Exitcode $AGENT_RC"

# 4. Gates unabhaengig vom Agenten nachpruefen. Sein Wort zaehlt hier nicht.
#    Die Rohausgabe wird mitgeschrieben: ein leeres Urteil (Absturz, kaputte
#    JSON mitten im Schreiben) sah im ersten Testlauf aus wie ein rotes Gate,
#    ohne zu verraten warum.
GRAPH_RAW="$("$PY" scripts/verify-graph.py 2>&1)"
NDA_RAW="$("$PY" scripts/verify-nda.py 2>&1)"
GRAPH="$(printf '%s' "$GRAPH_RAW" | tail -1)"
NDA="$(printf '%s' "$NDA_RAW" | grep -E '^(PASS|FAIL)' | tail -1)"
GATES_OK=1
case "$GRAPH" in graph=PASS*) ;; *) GATES_OK=0 ;; esac
case "$NDA" in PASS*) ;; *) GATES_OK=0 ;; esac

CHANGED="$(git status --porcelain -- distill notes | wc -l | tr -d ' ')"

if [ "$GATES_OK" -eq 0 ]; then
  say "ABBRUCH: Gate rot nach Destillation."
  say "--- verify-graph ---"
  say "$GRAPH_RAW"
  say "--- verify-nda ---"
  say "$NDA_RAW"
  # Arbeit NICHT verwerfen. Der Branch bleibt stehen, damit nachvollziehbar
  # ist was der Lauf getan hat; ohne PR landet nichts auf main. Nur die
  # Destillations-Pfade, fremde Aenderungen bleiben unberuehrt.
  git add distill notes 2>/dev/null
  git commit -q -m "wip: Destillat $TODAY, Gate rot, nicht zum Merge" 2>/dev/null
  git checkout -q "$START_BRANCH" 2>/dev/null
  say "Branch $BRANCH bleibt zur Ansicht stehen."
  exit 1
fi

# Ein rein privater Lauf aendert keinen getrackten Pfad, hat aber gearbeitet.
# Er darf hier NICHT aussteigen, sonst laeuft die unabhaengige Pruefung nie an
# und ausgerechnet das kundennahe Wissen geht ungeprueft durch.
PRIV_NEW=$(( $(priv_claims) - PRIV_BEFORE ))
if [ "$CHANGED" -eq 0 ] && [ "$PRIV_NEW" -eq 0 ]; then
  say "keine Aenderung an distill/ oder notes/, kein PR noetig"
  git checkout -q "$START_BRANCH" 2>/dev/null
  git branch -q -D "$BRANCH" 2>/dev/null
  exit 0
fi

# 5. Pruefen und reparieren, bis es merge-faehig ist.
#    Die Pruefinstanz hat den Destillationslauf nicht gesehen und soll ihn
#    widerlegen. Beanstandetes wird repariert, danach neu geprueft. Erst wenn
#    das Urteil sauber ist, wird uebernommen.
MAX_RUNDEN=3
RUNDE=1
VERDICT="problems"
PROBLEM_N=-1
PROBLEM_TXT=""

while [ "$RUNDE" -le "$MAX_RUNDEN" ]; do
  "$PY" scripts/new-claims.py origin/main "$PRIV_SNAPSHOT" \
    > /tmp/obsidiyan-new-claims.json 2>/dev/null
  NEW_N="$("$PY" -c "import json;print(len(json.load(open('/tmp/obsidiyan-new-claims.json'))))" 2>/dev/null || echo 0)"
  rm -f /tmp/obsidiyan-verdict.json

  say "--- Pruefung, Runde $RUNDE ($NEW_N neue Claims) ---"
  "$CLAUDE_BIN" -p "$(prompt_ohne_frontmatter scripts/verify-distillat-prompt.md)" \
    --allowedTools "Read" "Write" "Bash(.venv/bin/python:*)" "Bash(git status:*)" \
    --permission-mode acceptEdits \
    2>&1 | tail -3

  VERDICT="$("$PY" -c "
import json
try:
    print(json.load(open('/tmp/obsidiyan-verdict.json')).get('verdict','problems'))
except Exception:
    print('problems')
" 2>/dev/null)"
  PROBLEM_N="$("$PY" -c "
import json
try:
    print(len(json.load(open('/tmp/obsidiyan-verdict.json')).get('problems',[])))
except Exception:
    print(-1)
" 2>/dev/null)"
  PROBLEM_TXT="$("$PY" -c "
import json
try:
    for p in json.load(open('/tmp/obsidiyan-verdict.json')).get('problems',[]):
        print('- %s (%s): %s' % (p.get('claim_key','?'), p.get('issue','?'), p.get('detail','')))
except Exception:
    print('- Urteilsdatei fehlt oder ist unlesbar')
" 2>/dev/null)"

  if [ "$VERDICT" = "ok" ] && [ "$PROBLEM_N" = "0" ]; then
    say "Pruefung sauber in Runde $RUNDE"
    break
  fi

  if [ "$RUNDE" -eq "$MAX_RUNDEN" ]; then
    say "nach $MAX_RUNDEN Runden noch $PROBLEM_N Beanstandungen"
    break
  fi

  say "--- Reparatur, Runde $RUNDE ($PROBLEM_N Beanstandungen) ---"
  "$CLAUDE_BIN" -p "$(prompt_ohne_frontmatter scripts/repair-distillat-prompt.md)" \
    --allowedTools "Read" "Write" "Edit" "Bash(.venv/bin/python:*)" \
    --permission-mode acceptEdits \
    2>&1 | tail -3

  RUNDE=$((RUNDE + 1))
done

# 6. Gates nach der letzten Reparatur erneut, unabhaengig vom Urteil der Agenten.
GRAPH="$("$PY" scripts/verify-graph.py 2>&1 | tail -1)"
NDA="$("$PY" scripts/verify-nda.py 2>&1 | grep -E '^(PASS|FAIL)' | tail -1)"
case "$GRAPH" in graph=PASS*) ;; *) VERDICT="problems"; PROBLEM_TXT="- Graph-Gate rot nach Reparatur" ;; esac
case "$NDA" in PASS*) ;; *) VERDICT="problems"; PROBLEM_TXT="$PROBLEM_TXT
- NDA-Gate rot nach Reparatur" ;; esac

if [ "$(git status --porcelain -- distill notes | wc -l | tr -d ' ')" -eq 0 ]; then
  PRIV_NEW=$(( $(priv_claims) - PRIV_BEFORE ))
  git checkout -q "$START_BRANCH" 2>/dev/null
  git branch -q -D "$BRANCH" 2>/dev/null
  # Ein rein privater Lauf ist ein Erfolg ohne PR: private/ und der private
  # Claim-Store sind gitignored, es gibt schlicht nichts zu committen. Das
  # muss anders klingen als "nichts getan", sonst warnt der Tagesbericht
  # jeden Tag ueber eine Automation, die laeuft.
  if [ "$PRIV_NEW" -gt 0 ]; then
    say "privat uebernommen: $PRIV_NEW Claims nach private/, kein PR noetig weil gitignored"
  else
    say "nach Pruefung keine Aenderung uebrig, kein PR noetig"
  fi
  exit 0
fi

git add distill notes
git commit -q -m "docs(brain): automatisches Destillat vom $TODAY

Unbeaufsichtigter Lauf ueber $PENDING_N Kandidatenquellen, $NEW_N neue
Claims, $RUNDE Pruefrunden. Gates unabhaengig nachgeprueft:
$GRAPH
$NDA

Urteil der Pruefinstanz: $VERDICT ($PROBLEM_N Beanstandungen offen)"

git push -q -u origin "$BRANCH" 2>&1 | tail -1

# 7. Sauber heisst uebernehmen, ohne Rueckfrage. Der Merge ist der Audit-Trail.
if [ "$VERDICT" = "ok" ] && [ "$PROBLEM_N" = "0" ]; then
  PR_URL="$(gh pr create --base main --title "docs(brain): Destillat vom $TODAY" --body "Automatischer Lauf, $NEW_N neue Claims aus $PENDING_N Quellen, sauber nach $RUNDE Pruefrunde(n).

Gates: \`$GRAPH\` und \`$NDA\`
Unabhaengige Pruefung: keine Beanstandung.

Automatisch uebernommen. Rueckgaengig mit \`git revert\` auf diesen Merge.

Automatischer Lauf via scripts/auto-distill.sh" 2>&1 | tail -1)"
  if gh pr merge --merge --delete-branch "$PR_URL" >/dev/null 2>&1; then
    git checkout -q main 2>/dev/null
    git pull -q --ff-only 2>/dev/null
    say "uebernommen: $NEW_N Claims, $RUNDE Runde(n), keine Beanstandung"
    exit 0
  fi
  git checkout -q "$START_BRANCH" 2>/dev/null
  say "Merge fehlgeschlagen, PR offen: $PR_URL"
  exit 1
fi

# 8. Letzter Ausweg. Nach drei Runden nicht sauber heisst: hier stimmt etwas
#    Grundsaetzliches, und stilles Uebernehmen waere schlimmer als ein Hinweis.
PR_URL="$(gh pr create --base main --title "PRUEFUNG: Destillat vom $TODAY ($PROBLEM_N offen)" --body "Automatischer Lauf, $NEW_N neue Claims aus $PENDING_N Quellen. Nach $MAX_RUNDEN Pruef- und Reparaturrunden weiterhin beanstandet, deshalb kein automatischer Merge.

Gates: \`$GRAPH\` und \`$NDA\`

**Offen:**

$PROBLEM_TXT

Solange dieser PR offen ist, startet kein neuer Destillationslauf.

Automatischer Lauf via scripts/auto-distill.sh" 2>&1 | tail -1)"
git checkout -q "$START_BRANCH" 2>/dev/null
say "PRUEFUNG offen nach $MAX_RUNDEN Runden: $PROBLEM_N Beanstandungen, $PR_URL"
exit 0
