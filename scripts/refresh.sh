#!/usr/bin/env bash
# refresh.sh — Auto-Memory-Sync plus inkrementeller Ingest aller lokalen Quellen.
# Idempotent: der Watermark sorgt dafuer, dass nur Neues oder Geaendertes gelesen wird.
# Gedacht fuer Cron oder einen SessionEnd-Hook.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
if [ -z "${OBSIDIYAN_HOME:-}" ]; then
  printf 'OBSIDIYAN_HOME fehlt; ohne die Variable gilt das Engine-Verzeichnis als Vault\n' >&2
fi

# Nur ein Refresh gleichzeitig. Der SessionEnd-Hook feuert bei jedem
# Session-Ende, und mit einem Workspace-Manager enden regelmaessig mehrere
# Sessions dicht hintereinander. Watermark und Corpus-Dokumente werden mit
# write_text geschrieben, also nicht atomar: zwei parallele Laeufe koennen
# sich gegenseitig ueberschreiben. Zerstoerend ist das nicht, ein kaputter
# Watermark fuehrt nur zu vollem Neueinlesen, aber es kostet Zeit.
#
# Ueberspringen statt warten ist hier richtig: der Lauf ist idempotent und
# das naechste Session-Ende holt alles Fehlende ohnehin nach.
LOCK="${TMPDIR:-/tmp}/obsidiyan-refresh.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # Verwaister Lock nach Absturz: nach 30 Minuten uebernehmen.
  if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -30 2>/dev/null)" ]; then
    printf 'verwaister Lock aelter als 30 Minuten, wird uebernommen\n'
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { printf 'Lock nicht erlangbar, uebersprungen\n'; exit 0; }
  else
    printf 'Refresh laeuft bereits, dieser Lauf wird uebersprungen\n'
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# Der Corpus liest die kontrollierten Snapshots in memory/ und private/memory/.
# Vor jedem Ingest werden sie aus Claude Codes projektbezogener Auto-Memory
# aktualisiert; Routing-, Secret- und Pfad-Guards sitzen im Sync-Script.
bash bin/sync-memory.sh

# Archive-Dateien werden normalerweise als unveraendert uebersprungen. Sie sind
# trotzdem Teil jedes Laufs, damit eine geaenderte Parser- oder NDA-Policy alle
# sieben Quellen neu klassifiziert und nicht nur die Live-Session-Quellen.
#
# courses ist der Roh-Layer aus scripts/ingest-course.sh. Ohne diesen Schritt
# liegen die Transkripte auf der Platte und sind fuer die Suche unsichtbar.
for src in chatgpt gemini claude-web claude-code codex memory course; do
  "$PY" -m obsidiyan ingest --source "$src"
done
"$PY" scripts/verify-nda.py
"$PY" scripts/verify-graph.py
