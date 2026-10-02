#!/usr/bin/env bash
# Snapshot der Claude-Code Auto-Memory-Files ins gemeinsame Obsidian-Brain.
#
# Warum kopieren statt autoMemoryDirectory umbiegen:
# autoMemoryDirectory ist EIN globaler Key. Umbiegen wuerfe alle Projekte in
# einen gemeinsamen Ordner und zerstoerte die Trennung der Projekt-Memory.
# Ein Snapshot laesst Claudes Verhalten
# unveraendert und gibt trotzdem Versionierung und Durability.
# Clean-Projekte landen in memory/, NDA-Projekte im gitignorierten
# private/memory/. Damit ist Kundenwissen lokal auffindbar, ohne in git zu geraten.
#
# Idempotent. Read-only gegenueber ~/.claude.

set -euo pipefail
shopt -s nocasematch

SRC="${HOME}/.claude/projects"
# Vault wie in obsidiyan/paths.py: OBSIDIYAN_HOME, sonst das Verzeichnis ueber bin/.
BRAIN_ROOT="${OBSIDIYAN_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
DST="${BRAIN_ROOT}/memory"
PRIVATE_DST="${BRAIN_ROOT}/private/memory"

# Projekte, deren Memory NIEMALS in den git-getrackten Layer wandert.
# Ein Treffer als Substring im Projekt-Slug reicht.
DENY=(
  "acme-poc"
  "CRM-globex"
  "CRM_globex"
  "interviewkit"
  "Teamco-Website"
  "ini-tech"
  "Umbrella-Energie"
  "hooli-mag-website"
  "Globex-GmbH"
  "final-pr-acme"
  "acme-tooling"
)

# Derselbe Entity-Guard wie in obsidiyan/nda.py. Auch eine eigentlich saubere
# Projekt-Memory kann Kundenkontext enthalten. Solche Einzeldateien werden
# ebenfalls nur nach private/memory/ kopiert.
DENY_TERMS=(
  "acme"
  "globex"
  "docparse"
  "interviewkit"
  "storybot"
  "ini.tech"
  "ini-tech"
  "ini tech"
  "Umbrella Energie"
  "Umbrella-Energie"
  "hooli mag"
  "hooli-mag"
  "Teamco Website"
  "Teamco-Website"
)

is_denied() {
  local slug="$1"
  for pattern in "${DENY[@]}"; do
    [[ "$slug" == *"$pattern"* ]] && return 0
  done
  return 1
}

# Ein Projekt gilt als bekannt-unbedenklich, wenn im clean-Layer bereits ein
# Verzeichnis dafuer liegt. Bewusst keine vierte Liste: der bestehende Bestand
# IST die Liste, und was noch nie da war, ist per Default unbekannt.
project_is_known_clean() {
  local slug="$1"
  [ -d "${DST:?}/$slug" ]
}

file_is_denied() {
  local file="$1"
  local term
  for term in "${DENY_TERMS[@]}"; do
    grep -Fqi -- "$term" "$file" && return 0
  done
  # Clean im NDA-Sinn ist noch nicht automatisch versionierbar. Dateien mit
  # Mailadressen bleiben ebenfalls im privaten Layer.
  grep -Eiq '[[:alnum:]._%+~-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}' "$file" && return 0
  return 1
}

file_has_secret() {
  local file="$1"
  grep -Eiq '(sk-ant-[[:alnum:]_-]{20,}|sk-[[:alnum:]_-]{20,}|github_pat_[[:alnum:]_]{20,}|gh[pousr]_[[:alnum:]]{20,}|AKIA[0-9A-Z]{16}|AIza[[:alnum:]_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' "$file"
}

unsafe_path() {
  printf 'FEHLER: unsicherer Brain-Zielpfad (Symlink oder Escape): %s\n' "$1" >&2
  exit 1
}

unsafe_source() {
  printf 'FEHLER: unsicherer Auto-Memory-Quellpfad (Symlink oder Escape): %s\n' "$1" >&2
  exit 1
}

assert_safe_source() {
  local path="$1"
  local current="$SRC"
  local relative component parent resolved_candidate

  case "$path" in
    "$SRC"|"$SRC"/*) ;;
    *) unsafe_source "$path" ;;
  esac

  [ ! -L "$SRC" ] || unsafe_source "$SRC"
  [ "$path" != "$SRC" ] || return 0

  relative="${path#"$SRC"/}"
  while [ -n "$relative" ]; do
    component="${relative%%/*}"
    if [ "$relative" = "$component" ]; then
      relative=""
    else
      relative="${relative#*/}"
    fi
    case "$component" in
      ""|.) continue ;;
      ..) unsafe_source "$path" ;;
    esac
    current="$current/$component"
    [ ! -L "$current" ] || unsafe_source "$current"
  done

  if [ -d "$path" ]; then
    resolved_candidate="$(cd -P "$path" && pwd)"
  else
    parent="$(dirname "$path")"
    resolved_candidate="$(cd -P "$parent" && pwd)/$(basename "$path")"
  fi
  case "$resolved_candidate" in
    "$SOURCE_ROOT_RESOLVED"|"$SOURCE_ROOT_RESOLVED"/*) ;;
    *) unsafe_source "$path" ;;
  esac
}

assert_safe_target() {
  local path="$1"
  local current="$BRAIN_ROOT"
  local relative component

  case "$path" in
    "$BRAIN_ROOT"|"$BRAIN_ROOT"/*) ;;
    *) unsafe_path "$path" ;;
  esac

  [ ! -L "$BRAIN_ROOT" ] || unsafe_path "$BRAIN_ROOT"
  [ "$path" != "$BRAIN_ROOT" ] || return 0

  relative="${path#"$BRAIN_ROOT"/}"
  while [ -n "$relative" ]; do
    component="${relative%%/*}"
    if [ "$relative" = "$component" ]; then
      relative=""
    else
      relative="${relative#*/}"
    fi
    case "$component" in
      ""|.) continue ;;
      ..) unsafe_path "$path" ;;
    esac
    current="$current/$component"
    [ ! -L "$current" ] || unsafe_path "$current"
  done
}

safe_mkdir() {
  local directory="$1"
  assert_safe_target "$directory"
  mkdir -p "$directory"
  assert_safe_target "$directory"
}

prepare_target() {
  local target="$1"
  local parent resolved_brain resolved_parent
  assert_safe_target "$target"
  parent="$(dirname "$target")"
  safe_mkdir "$parent"
  assert_safe_target "$target"

  resolved_brain="$(cd -P "$BRAIN_ROOT" && pwd)"
  resolved_parent="$(cd -P "$parent" && pwd)"
  case "$resolved_parent" in
    "$resolved_brain"|"$resolved_brain"/*) ;;
    *) unsafe_path "$target" ;;
  esac
}

atomic_copy() {
  local source="$1"
  local destination="$2"
  local temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  if ! cp -p "$source" "$temporary"; then
    rm -f "$temporary"
    return 1
  fi
  if ! mv -f "$temporary" "$destination"; then
    rm -f "$temporary"
    return 1
  fi
}

[ ! -L "$BRAIN_ROOT" ] || unsafe_path "$BRAIN_ROOT"
mkdir -p "$BRAIN_ROOT"
safe_mkdir "$DST"
safe_mkdir "$PRIVATE_DST"

SOURCE_ROOT_RESOLVED=""
if [ -e "$SRC" ] || [ -L "$SRC" ]; then
  [ -d "$SRC" ] || unsafe_source "$SRC"
  [ ! -L "$SRC" ] || unsafe_source "$SRC"
  SOURCE_ROOT_RESOLVED="$(cd -P "$SRC" && pwd)"
fi

clean_projects=0
private_projects=0
clean_files=0
private_files=0
unknown_projects=""

for memdir in "$SRC"/*/memory; do
  [ ! -L "$memdir" ] || unsafe_source "$memdir"
  [ -d "$memdir" ] || continue
  assert_safe_source "$memdir"

  slug="$(basename "$(dirname "$memdir")")"
  slug="${slug#-Users-demo-}"
  [ -n "$slug" ] || slug="_home"

  # Fail-safe: ein Projekt, das der Sync noch nie gesehen hat, wird wie
  # vertraulich behandelt. Der teure Fehler ist Kundenmaterial im getrackten
  # Layer, nicht ein eigenes Projekt einen Lauf lang im privaten. Freigeben
  # heisst: einmal `mkdir memory/<slug>` und der naechste Lauf zaehlt es clean.
  slug_unknown=0
  if ! is_denied "$slug" && ! project_is_known_clean "$slug"; then
    slug_unknown=1
    unknown_projects="${unknown_projects}${unknown_projects:+ }${slug}"
  fi

  project_clean=0
  project_private=0
  for source_file in "$memdir"/*.md; do
    if [ -L "$source_file" ]; then
      printf 'FEHLER: Auto-Memory-Quelle ist ein Symlink: %s\n' "$source_file" >&2
      exit 1
    fi
    [ -f "$source_file" ] || continue
    assert_safe_source "$source_file"
    filename="$(basename "$source_file")"
    clean_target="$DST/$slug/$filename"
    private_target="$PRIVATE_DST/$slug/$filename"

    assert_safe_target "$clean_target"
    assert_safe_target "$private_target"

    if file_has_secret "$source_file"; then
      printf 'FEHLER: moegliches Secret in Auto-Memory: %s\n' "$source_file" >&2
      exit 1
    fi

    if [ "$slug_unknown" -eq 1 ] || is_denied "$slug" || file_is_denied "$source_file" || [ -f "$private_target" ]; then
      if [ -f "$clean_target" ]; then
        printf 'FEHLER: NDA-Memory liegt noch im clean-Layer: %s\n' "$clean_target" >&2
        exit 1
      fi
      prepare_target "$private_target"
      atomic_copy "$source_file" "$private_target"
      project_private=$((project_private + 1))
      private_files=$((private_files + 1))
    else
      prepare_target "$clean_target"
      atomic_copy "$source_file" "$clean_target"
      project_clean=$((project_clean + 1))
      clean_files=$((clean_files + 1))
    fi
  done

  [ "$project_clean" -eq 0 ] || clean_projects=$((clean_projects + 1))
  [ "$project_private" -eq 0 ] || private_projects=$((private_projects + 1))
  printf '  %-46s %3s clean, %3s private\n' "$slug" "$project_clean" "$project_private"
done

printf '\n%s Projekte mit clean Memory (%s Files), %s mit private Memory (%s Files) kopiert\n' \
  "$clean_projects" "$clean_files" "$private_projects" "$private_files"

if [ -n "$unknown_projects" ]; then
  printf 'UNBEKANNTE PROJEKTE (vorsorglich privat): %s\n' "$unknown_projects"
  printf '  Freigeben: mkdir -p memory/<slug> und private/memory/<slug> loeschen.\n'
  printf '  Beides noetig, weil eine bereits privat abgelegte Datei sonst privat bleibt.\n'
fi
