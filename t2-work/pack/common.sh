# shellcheck shell=bash disable=SC2034  # variables here are used by the scripts that source this file
# Sourced by every t2-work/pack/*.sh. Paths, configuration and small helpers; no side effects
# beyond setting variables. Works in Git Bash on Windows and in plain bash on Linux/macOS.
set -euo pipefail

# Absolute path in "mixed" form (C:/Users/...) on Git Bash, plain POSIX elsewhere. Bash, native
# Windows python.exe and docker.exe all accept the mixed form, so nothing below needs
# per-command path conversion.
abspath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }

PACK_DIR="$(abspath "$(dirname "${BASH_SOURCE[0]}")")"
T2_DIR="$(abspath "$PACK_DIR/..")"
ROOT_DIR="$(abspath "$T2_DIR/..")"
TRACK_DIR="$ROOT_DIR/track2-forecasting-public"
UNITS_DIR="$TRACK_DIR/units"
STATE_DIR="$PACK_DIR/state"
VENV_DIR="$PACK_DIR/.venv-docker"
LOCK_FILE="$T2_DIR/requirements.lock"
DESCRIPTOR_DEFAULT="$T2_DIR/submission/submission.json"

# Image names. Override any of these from the environment if needed.
: "${LOCAL_REPO:=jinpei-t2}"                       # local tag repository
: "${GHCR_OWNER:=dapeipeipeipei}"                 # GitHub account that owns the package
: "${GHCR_NAME:=jinpei-t2}"                       # package (image) name on ghcr.io
GHCR_REPO="$GHCR_OWNER/$GHCR_NAME"
TOOLKIT_TAG="v2.6.0"
TOOLKIT_URL="https://github.com/Agenthon-2026/Agenthon2026-public/archive/refs/tags/${TOOLKIT_TAG}.tar.gz#subdirectory=common"

export PYTHONUTF8=1

die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

# docker with Git Bash's argument rewriting switched off: without this, MSYS turns container
# paths such as /input or /output:rw into C:/Program Files/Git/input and the mount is wrong.
dk() { MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker "$@"; }

docker_ok() { dk version --format '{{.Server.Version}}' >/dev/null 2>&1; }

require_docker() {
  docker_ok || die "Docker engine not reachable. Start Docker Desktop, wait until it says 'Engine running', then retry."
}

# The Python used for checking outputs and packing: the pinned .venv-docker if it exists,
# otherwise the main dev .venv.
pick_python() {
  local c
  for c in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/bin/python" \
           "$ROOT_DIR/.venv/Scripts/python.exe" "$ROOT_DIR/.venv/bin/python"; do
    if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  die "no Python environment found; run t2-work/pack/make_venv.sh first"
}

# Read KEY=VALUE state written by build.sh / push.sh (plain assignments only, never sourced).
state_get() {
  local file="$STATE_DIR/$1" key="$2"
  [ -f "$file" ] || return 1
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

state_put() {   # state_put <file> KEY=VALUE ...
  local file="$STATE_DIR/$1"; shift
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$@" > "$file"
}

is_digest() { [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]; }
