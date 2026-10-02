#!/usr/bin/env bash
# Vollstaendiger, wiederherstellbarer Corpus-Rebuild.
# Der bisherige abgeleitete Corpus bleibt in einem temporaeren Backup erhalten.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || {
  echo "rebuild: .venv fehlt" >&2
  exit 1
}

backup_dir="$(mktemp -d /tmp/obsidiyan-corpus-backup.XXXXXX)"
if [ -d corpus ]; then
  mv corpus "$backup_dir/corpus"
fi
mkdir corpus

for source_name in chatgpt gemini claude-web claude-code codex memory; do
  if ! "$PY" -m obsidiyan ingest --source "$source_name"; then
    mv corpus "$backup_dir/failed-corpus"
    if [ -d "$backup_dir/corpus" ]; then
      mv "$backup_dir/corpus" corpus
    fi
    printf 'REBUILD FAILED. Alter Stand wiederhergestellt; Diagnose: %s\n' "$backup_dir" >&2
    exit 1
  fi
done

if ! "$PY" scripts/verify-nda.py; then
  mv corpus "$backup_dir/failed-corpus"
  if [ -d "$backup_dir/corpus" ]; then
    mv "$backup_dir/corpus" corpus
  fi
  printf 'AUDIT FAILED. Alter Stand wiederhergestellt; Diagnose: %s\n' "$backup_dir" >&2
  exit 1
fi

printf 'REBUILD OK. Vorheriger Corpus als temporaeres Backup: %s\n' "$backup_dir"
