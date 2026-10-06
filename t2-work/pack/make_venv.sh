#!/usr/bin/env bash
# Create (or refresh) t2-work/pack/.venv-docker: a local Python 3.13 environment with EXACTLY the
# library pins the submission image uses (t2-work/requirements.lock), plus what the local checks
# need on top -- the shared toolkit (qfbench2 CLI, output-tree policy) and the track scorer.
#
#   bash t2-work/pack/make_venv.sh            # create if missing, install, verify the pins
#   bash t2-work/pack/make_venv.sh --recreate # delete and rebuild from scratch
#
# The toolkit and scorer are installed with the lock as a constraint, so they cannot move a pin.
# Their extra dependencies (scipy, jsonschema) are for scoring only and are NOT in the image.
source "$(dirname "$0")/common.sh"

recreate=0
for a in "$@"; do
  case "$a" in
    --recreate) recreate=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

if [ "$recreate" = 1 ] && [ -d "$VENV_DIR" ]; then
  info "removing $VENV_DIR"; rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
  if command -v py >/dev/null 2>&1; then
    info "creating venv with: py -3.13"; py -3.13 -m venv "$VENV_DIR"
  elif command -v python3.13 >/dev/null 2>&1; then
    info "creating venv with: python3.13"; python3.13 -m venv "$VENV_DIR"
  elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))'; then
    info "creating venv with: python3 (3.13)"; python3 -m venv "$VENV_DIR"
  else
    die "Python 3.13 not found (need 'py -3.13' on Windows or 'python3.13')"
  fi
fi

if [ -x "$VENV_DIR/Scripts/python.exe" ]; then PY="$VENV_DIR/Scripts/python.exe"; else PY="$VENV_DIR/bin/python"; fi
"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 13), sys.version' \
  || die "the venv is not Python 3.13; rerun with --recreate"

info "installing the image pins from $LOCK_FILE"
"$PY" -m pip install --disable-pip-version-check -q -r "$LOCK_FILE"
info "installing the toolkit qfbench2-common $TOOLKIT_TAG (constrained by the lock)"
"$PY" -m pip install --disable-pip-version-check -q -c "$LOCK_FILE" "qfbench2-common @ $TOOLKIT_URL"
info "installing the track scorer package (editable, no deps) from $TRACK_DIR"
"$PY" -m pip install --disable-pip-version-check -q --no-deps -e "$TRACK_DIR"

info "verifying installed versions against the lock"
"$PY" - "$LOCK_FILE" <<'EOF'
import sys, importlib.metadata as md
bad = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, want = line.split("==")
    have = md.version(name)
    print(f"  {name:16s} {have:14s} {'ok' if have == want else 'MISMATCH (lock ' + want + ')'}")
    if have != want:
        bad.append(name)
print(f"  {'qfbench2-common':16s} {md.version('qfbench2-common')}")
sys.exit(1 if bad else 0)
EOF
info "done: $PY"
