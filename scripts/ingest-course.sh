#!/usr/bin/env bash
# ingest-course.sh — Video-Kurs zu durchsuchbarem Text machen.
#
# Nimmt einen lokal gespiegelten Kursordner (Videos + Notebooks + PDFs) und legt
# unter ~/Obsidiyan/courses/<slug>/ einen 1:1 gespiegelten Textbaum an:
#   Video  -> ffmpeg (16k mono wav) -> mlx-whisper -> .md mit Frontmatter
#   Asset  -> Kopie (ipynb, py, pdf, md, txt, csv, json)
#
# courses/ ist der Roh-Layer analog zu books/: gitignored, urheberrechtlich
# geschuetzt, nie committen, nie laengere Passagen zitieren. Destillate gehen
# nach notes/ oder ins jeweilige Projekt-Repo.
#
# Idempotent: fertige Transkripte werden uebersprungen. Abbrechbar und
# fortsetzbar; ein zweiter Lauf macht nur das Fehlende.
#
# Nutzung:
#   scripts/ingest-course.sh --src ~/Downloads/mein-kurs --slug mein-kurs
#   scripts/ingest-course.sh --src ~/Downloads/TPQ --slug tpq-cpf-2024 --prune
#
# --prune loescht die Quelldatei nach erfolgreichem Transkript. Nur benutzen,
# wenn das Original noch in Drive/MEGA liegt; Platte ist der Engpass, nicht die
# Quelle.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHISPER_VENV="${WHISPER_VENV:-$HOME/.cache/media-transcribe/venv}"
MODEL="${COURSE_WHISPER_MODEL:-mlx-community/whisper-large-v3-turbo}"
LANG_ARG="en"
SRC=""
SLUG=""
PRUNE=0
DRY_RUN=0

# ts ist hier MPEG-TS-Video (HLS-Segmente), nicht TypeScript. Der
# Audiostream-Check unten sortiert echte TypeScript-Dateien wieder aus.
MEDIA_EXT="mp4|mkv|mov|m4v|webm|avi|ts|mp3|m4a|wav|flac|aac|ogg"
ASSET_EXT="ipynb|py|pdf|md|txt|csv|json|sql|r|yaml|yml|docx|doc|html|htm|xlsx|pptx|toml|cfg|ini|rst"

die() { printf 'ingest-course: %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --src)      SRC="${2:-}"; shift 2 ;;
    --slug)     SLUG="${2:-}"; shift 2 ;;
    --model)    MODEL="${2:-}"; shift 2 ;;
    --lang)     LANG_ARG="${2:-}"; shift 2 ;;
    --prune)    PRUNE=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)          die "unbekannte Option: $1" ;;
  esac
done

[ -n "$SRC" ]  || die "--src fehlt"
[ -n "$SLUG" ] || die "--slug fehlt"
[ -d "$SRC" ]  || die "--src ist kein Verzeichnis: $SRC"
[ -x "$WHISPER_VENV/bin/mlx_whisper" ] || die "mlx_whisper fehlt in $WHISPER_VENV (media-transcribe Skill einmal laufen lassen)"
command -v ffmpeg >/dev/null || die "ffmpeg fehlt (brew install ffmpeg)"

SRC="$(cd "$SRC" && pwd)"
OUT_ROOT="$REPO_ROOT/courses/$SLUG"
mkdir -p "$OUT_ROOT"
MANIFEST="$OUT_ROOT/.manifest.tsv"
[ -f "$MANIFEST" ] || printf 'status\tseconds\trel_path\n' > "$MANIFEST"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ingest-course.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Frontmatter-Werte duerfen den YAML-Parser nicht sprengen.
yaml_escape() { printf '%s' "$1" | sed "s/'/''/g"; }

media_seconds() {
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null \
    | awk '{printf "%d", ($1 == "" ? 0 : $1)}'
}

# BSD-find kennt kein `-iregex` mit Alternation, deshalb wird die
# Extension-Liste in eine -iname-Kette uebersetzt. Portabel und lesbar.
FIND_NAME_ARGS=()
for ext in $(printf '%s\n' "$MEDIA_EXT" "$ASSET_EXT" | tr '|' ' '); do
  [ ${#FIND_NAME_ARGS[@]} -eq 0 ] || FIND_NAME_ARGS+=(-o)
  FIND_NAME_ARGS+=(-iname "*.${ext}")
done
for bare in LICENSE COPYING README NOTICE AUTHORS CHANGELOG; do
  FIND_NAME_ARGS+=(-o -iname "$bare" -o -iname "${bare}.*")
done

total=0; done_n=0; skip_n=0; fail_n=0; asset_n=0; secs_total=0

# -print0 statt Wortsplit: Kursdateien haben Leerzeichen und Klammern im Namen.
while IFS= read -r -d '' src_file; do
  rel="${src_file#"$SRC"/}"
  total=$((total + 1))
  base="$(basename "$src_file")"
  case "$base" in
    *.*) ext="${base##*.}" ;;
    *)   ext="" ;;
  esac
  ext="$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')"

  if [ -z "$ext" ] || printf '%s' "$ext" | grep -qE "^($ASSET_EXT)$"; then
    dest="$OUT_ROOT/$rel"
    # cp -p erhaelt die mtime, dest ist also nie *neuer* als src. Der Test muss
    # deshalb "src nicht neuer als dest" lauten, sonst kopiert jeder Lauf alles neu.
    if [ -f "$dest" ] && ! [ "$src_file" -nt "$dest" ]; then
      skip_n=$((skip_n + 1)); continue
    fi
    asset_n=$((asset_n + 1))
    [ "$DRY_RUN" -eq 1 ] && { printf 'ASSET  %s\n' "$rel"; continue; }
    mkdir -p "$(dirname "$dest")"
    cp -p "$src_file" "$dest"
    printf 'asset\t0\t%s\n' "$rel" >> "$MANIFEST"
    continue
  fi

  rel_dir="$(dirname "$rel")"
  rel_stem="${base%.*}"
  if [ "$rel_dir" = "." ]; then dest="$OUT_ROOT/$rel_stem.md"; else dest="$OUT_ROOT/$rel_dir/$rel_stem.md"; fi
  if [ -s "$dest" ]; then
    skip_n=$((skip_n + 1)); continue
  fi

  [ "$DRY_RUN" -eq 1 ] && { printf 'MEDIA  %s\n' "$rel"; continue; }

  # ffprobe-Fehler und "Datei hat keinen Audiostream" sind zwei verschiedene
  # Dinge. Beides als skip-noaudio zu behandeln verliert unter Last still
  # echte Videos, deshalb wird der Exit-Code getrennt ausgewertet.
  if probe_out="$(ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "$src_file" 2>/dev/null)"; then
    probe_rc=0
  else
    probe_rc=$?
  fi
  if [ "$probe_rc" -ne 0 ]; then
    printf '  FEHLER: ffprobe rc=%s, Datei bleibt liegen\n' "$probe_rc" >&2
    printf 'fail-ffprobe\t0\t%s\n' "$rel" >> "$MANIFEST"
    fail_n=$((fail_n + 1)); continue
  fi
  if ! printf '%s' "$probe_out" | grep -q audio; then
    printf 'skip-noaudio\t0\t%s\n' "$rel" >> "$MANIFEST"
    skip_n=$((skip_n + 1)); continue
  fi

  printf '[%s] %s\n' "$SLUG" "$rel"
  wav="$WORK/audio.wav"
  rm -f "$wav"
  if ! ffmpeg -nostdin -v error -y -i "$src_file" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$wav"; then
    printf '  FEHLER: ffmpeg konnte kein Audio extrahieren\n' >&2
    printf 'fail-ffmpeg\t0\t%s\n' "$rel" >> "$MANIFEST"
    fail_n=$((fail_n + 1)); continue
  fi

  secs="$(media_seconds "$src_file")"
  rm -f "$WORK/out.txt"
  if ! "$WHISPER_VENV/bin/mlx_whisper" "$wav" \
        --model "$MODEL" --language "$LANG_ARG" --task transcribe \
        --output-dir "$WORK" --output-name out --output-format txt \
        --verbose False >/dev/null; then
    printf '  FEHLER: mlx_whisper abgebrochen\n' >&2
    printf 'fail-whisper\t%s\t%s\n' "$secs" "$rel" >> "$MANIFEST"
    fail_n=$((fail_n + 1)); continue
  fi
  [ -s "$WORK/out.txt" ] || {
    printf '  FEHLER: leeres Transkript\n' >&2
    printf 'fail-empty\t%s\t%s\n' "$secs" "$rel" >> "$MANIFEST"
    fail_n=$((fail_n + 1)); continue
  }

  module="$(dirname "$rel")"; [ "$module" = "." ] && module="(root)"
  lesson="$rel_stem"
  mkdir -p "$(dirname "$dest")"
  # Erst in eine Temp-Datei, dann mv: ein Abbruch mittendrin darf kein halbes
  # .md hinterlassen, das der naechste Lauf als "fertig" ueberspringt.
  tmp_md="$WORK/doc.md"
  {
    printf -- '---\n'
    printf "source: course\n"
    printf "course: '%s'\n" "$(yaml_escape "$SLUG")"
    printf "module: '%s'\n" "$(yaml_escape "$module")"
    printf "lesson: '%s'\n" "$(yaml_escape "$lesson")"
    printf "media_path: '%s'\n" "$(yaml_escape "$rel")"
    printf "duration_seconds: %s\n" "$secs"
    printf "asr_model: '%s'\n" "$(yaml_escape "$MODEL")"
    printf "sensitivity: copyright\n"
    printf -- '---\n\n'
    printf '# %s\n\n' "$lesson"
    cat "$WORK/out.txt"
    printf '\n'
  } > "$tmp_md"
  mv "$tmp_md" "$dest"

  printf 'ok\t%s\t%s\n' "$secs" "$rel" >> "$MANIFEST"
  done_n=$((done_n + 1))
  secs_total=$((secs_total + secs))
  [ "$PRUNE" -eq 1 ] && rm -f "$src_file"
done < <(find "$SRC" -type f \( "${FIND_NAME_ARGS[@]}" \) -print0 | sort -z)

printf '\ningest-course %s: %d Dateien gesehen, %d transkribiert (%d min Audio), %d Assets kopiert, %d uebersprungen, %d Fehler\n' \
  "$SLUG" "$total" "$done_n" "$((secs_total / 60))" "$asset_n" "$skip_n" "$fail_n"
printf 'Ziel: %s\nManifest: %s\n' "$OUT_ROOT" "$MANIFEST"
[ "$fail_n" -eq 0 ] || exit 1
