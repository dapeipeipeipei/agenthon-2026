#!/usr/bin/env bash
# Put the pushed image digest into submission.json and build submission.zip with the toolkit.
#
#   bash t2-work/pack/pack.sh --team-number <N> [--digest sha256:<64 hex>]
# Options:
#   --team-number N   your website team number (NOT secret; required)
#   --digest D        image digest (default: DIGEST from pack/state/pushed.env)
#   --repo OWNER/NAME image repository on ghcr.io (default: from pushed.env, else dapeipeipeipei/jinpei-t2)
#   --descriptor P    default t2-work/submission/submission.json
#   --out P           default t2-work/pack/dist/submission.zip
#   --check-only      fill + validate the descriptor, do not pack (no Team Key needed)
#   --skip-pull-check do not re-run the anonymous pull check first (not recommended)
#
# The Team Key is typed by YOU at the toolkit's hidden prompt ("Team Key (hidden): "). This script
# never reads, stores, logs or passes it; there is deliberately no option for it.
source "$(dirname "$0")/common.sh"

team=""; digest=""; repo=""; desc="$DESCRIPTOR_DEFAULT"; out="$PACK_DIR/dist/submission.zip"
check_only=0; pull_check=1
while [ $# -gt 0 ]; do
  case "$1" in
    --team-number) team="$2"; shift ;;
    --digest) digest="$2"; shift ;;
    --repo) repo="$2"; shift ;;
    --descriptor) desc="$2"; shift ;;
    --out) out="$2"; shift ;;
    --check-only) check_only=1 ;;
    --skip-pull-check) pull_check=0 ;;
    --team-key*|--key*) die "the Team Key is never passed on the command line; you will be asked for it at a hidden prompt" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[ -n "$digest" ] || digest="$(state_get pushed.env DIGEST || true)"
[ -n "$repo" ] || repo="$(state_get pushed.env REPOSITORY || true)"
[ -n "$repo" ] || repo="$GHCR_REPO"
[ -n "$digest" ] || die "no digest: pass --digest sha256:<64 hex> (from push.sh or the CI run summary)"
is_digest "$digest" || die "not a digest: $digest"
[ -f "$desc" ] || die "descriptor not found: $desc (it is drafted separately; ask for t2-work/submission/submission.json)"
if [ "$check_only" = 0 ]; then
  [[ "$team" =~ ^[1-9][0-9]*$ ]] || die "--team-number <N> is required (your website team number, digits only)"
fi

PY="$(pick_python)"
"$PY" -c 'import qfbench2_common' 2>/dev/null || die "toolkit missing in $PY; run: bash t2-work/pack/make_venv.sh"
tk="$("$PY" -c 'import importlib.metadata as m; print(m.version("qfbench2-common"))')"
[ "$tk" = "${TOOLKIT_TAG#v}" ] || warn "toolkit in $PY is $tk, expected ${TOOLKIT_TAG#v}"

# 1. Anonymous pullability of exactly this digest (the organizer pulls it without our credentials).
if [ "$pull_check" = 1 ]; then
  info "step 1/4: anonymous pull check"
  bash "$PACK_DIR/verify_anonymous_pull.sh" --repo "$repo" --digest "$digest" \
    || die "the image is not anonymously pullable yet; fix that before packing (README step 6)"
fi

# 2. Fill the digest into the descriptor (only the image object changes) and validate it.
info "step 2/4: fill image digest into $desc and validate with the toolkit"
cp -p "$desc" "$desc.bak"
"$PY" "$PACK_DIR/descriptor_tool.py" fill --descriptor "$desc" --registry ghcr.io \
      --repository "$repo" --digest "$digest" \
  || { mv -f "$desc.bak" "$desc"; die "descriptor invalid; original restored, nothing packed"; }
rm -f "$desc.bak"
if [ "$check_only" = 1 ]; then info "check-only: descriptor filled and valid; not packing"; exit 0; fi

# 3. Seal + claim + zip by the toolkit. It prompts for the Team Key with echo off.
mkdir -p "$(dirname "$out")"
info "step 3/4: qfbench2 submission pack (team number $team)"
echo "        You will now be asked:  Team Key (hidden):"
echo "        Type or paste the key and press Enter. Nothing appears while you type; that is expected."
pack_in="$(dirname "$out")/submission.pack-input.json"
"$PY" "$PACK_DIR/descriptor_tool.py" pack-input --descriptor "$desc" --out "$pack_in"
pack_cmd=("$PY" -m qfbench2_common.cli submission pack --descriptor "$pack_in" --team-number "$team" --out "$out" --force)
if "$PY" -c 'import sys; sys.exit(0 if sys.stdin.isatty() else 1)'; then
  "${pack_cmd[@]}"
elif [ -n "${MSYSTEM:-}" ] && command -v winpty >/dev/null 2>&1; then
  # Git Bash (mintty) gives native Windows programs a pipe, not a console; winpty supplies one
  # so the toolkit's hidden prompt works.
  winpty "${pack_cmd[@]}"
else
  echo
  warn "this terminal cannot hide input, so the toolkit would refuse to ask for the key here."
  echo "Run this one line in PowerShell instead (then rerun pack.sh with --skip-pull-check to verify):"
  printf '  & "%s" -m qfbench2_common.cli submission pack --descriptor "%s" --team-number %s --out "%s" --force\n' \
    "$(cygpath -w "$PY" 2>/dev/null || echo "$PY")" "$(cygpath -w "$pack_in" 2>/dev/null || echo "$pack_in")" \
    "$team" "$(cygpath -w "$out" 2>/dev/null || echo "$out")"
  exit 2
fi
[ -f "$out" ] || die "pack did not write $out"

# 4. Independent check of the zip: two members, digest, derived team_id, claim bound to the bytes.
info "step 4/4: verify $out"
"$PY" "$PACK_DIR/descriptor_tool.py" verify-zip --zip "$out" --digest "$digest" --repository "$repo" \
  || die "the zip failed verification; do NOT upload it"
echo
info "READY: upload this file on the Track 2 CodaBench page:  $out"
