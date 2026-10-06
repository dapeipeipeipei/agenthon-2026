#!/usr/bin/env bash
# Build the Track 2 submission image for linux/amd64, tag it by content, and self-check it.
#
#   bash t2-work/pack/build.sh             # build + checks; writes pack/state/image.env
#   bash t2-work/pack/build.sh --dry-run   # static checks + print the build command, no Docker needed
#   bash t2-work/pack/build.sh --no-cache  # rebuild every layer
#
# Tags produced (local only, nothing is pushed):
#   jinpei-t2:dev                          always the latest build
#   jinpei-t2:<git rev>[-dirty]-<id12>     immutable-by-content: <id12> = first 12 hex of the image ID
# The REGISTRY digest (sha256:... used in submission.json) only exists after push.sh.
source "$(dirname "$0")/common.sh"

dry=0; extra=()
for a in "$@"; do
  case "$a" in
    --dry-run) dry=1 ;;
    --no-cache) extra+=(--no-cache) ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

DOCKERFILE="$T2_DIR/Dockerfile"

# ---------------------------------------------------------------- static checks (no Docker needed)
info "static checks on $DOCKERFILE"
fail=0
check() { if eval "$2"; then printf '  ok    %s\n' "$1"; else printf '  FAIL  %s\n' "$1"; fail=1; fi; }
check "LABEL qfbench2.interface_version=\"2.0\""  "grep -Eq '^LABEL qfbench2\.interface_version=\"2\.0\"' '$DOCKERFILE'"
check "no ENTRYPOINT (verb resolved on PATH)"     "! grep -Eq '^ENTRYPOINT' '$DOCKERFILE'"
check "verb script /usr/local/bin/forecast"       "grep -q '/usr/local/bin/forecast' '$DOCKERFILE'"
check "non-root USER"                             "grep -Eq '^USER +[1-9][0-9]*' '$DOCKERFILE'"
check "base image pinned by digest"               "grep -Eq '^FROM .+@sha256:[0-9a-f]{64}' '$DOCKERFILE'"
check "toolkit pinned to $TOOLKIT_TAG tarball"    "grep -q 'refs/tags/${TOOLKIT_TAG}.tar.gz' '$DOCKERFILE'"
check "requirements.lock present, all pins =="    "[ -f '$LOCK_FILE' ] && ! grep -Ev '^\s*(#|$)' '$LOCK_FILE' | grep -vq '=='"
check ".dockerignore present (allowlist)"         "[ -f '$T2_DIR/.dockerignore' ] && head -n 20 '$T2_DIR/.dockerignore' | grep -qx '\*'"
check "engine/forecast.py present"                "[ -f '$T2_DIR/engine/forecast.py' ]"
[ "$fail" = 0 ] || die "static checks failed"

rev="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo nogit)"
dirty=""
if [ -n "$(git -C "$ROOT_DIR" status --porcelain -- t2-work/engine t2-work/Dockerfile t2-work/requirements.lock 2>/dev/null)" ]; then
  dirty="-dirty"
  warn "engine/Dockerfile/lock have uncommitted changes; the tag will say '-dirty'. Commit first for a submission build."
fi
DEV_TAG="$LOCAL_REPO:dev"

cmd=(buildx build --platform linux/amd64 --provenance=false --sbom=false --load
     --label "org.opencontainers.image.revision=${rev}${dirty}"
     -t "$DEV_TAG" -f "$DOCKERFILE" "${extra[@]}" "$T2_DIR")

if [ "$dry" = 1 ]; then
  info "dry run; would execute:"
  printf '  docker'; printf ' %q' "${cmd[@]}"; printf '\n'
  exit 0
fi

require_docker
info "building (first build downloads ~100 MB of wheels; later builds reuse the cache)"
t0=$(date +%s)
dk "${cmd[@]}"
info "build took $(( $(date +%s) - t0 ))s"

# ---------------------------------------------------------------- inspect + self-check the result
id="$(dk image inspect --format '{{.Id}}' "$DEV_TAG")"
is_digest "$id" || die "unexpected image id: $id"
CONTENT_TAG="$LOCAL_REPO:${rev}${dirty}-${id:7:12}"
dk tag "$DEV_TAG" "$CONTENT_TAG"

platform="$(dk image inspect --format '{{.Os}}/{{.Architecture}}' "$DEV_TAG")"
label="$(dk image inspect --format '{{index .Config.Labels "qfbench2.interface_version"}}' "$DEV_TAG")"
user="$(dk image inspect --format '{{.Config.User}}' "$DEV_TAG")"
entry="$(dk image inspect --format '{{json .Config.Entrypoint}}' "$DEV_TAG")"
size="$(dk image inspect --format '{{.Size}}' "$DEV_TAG")"

fail=0
check "platform linux/amd64 (got $platform)"        "[ '$platform' = linux/amd64 ]"
check "label interface_version 2.0 (got $label)"     "[ '$label' = 2.0 ]"
check "non-root user (got $user)"                    "[ -n '$user' ] && [ '${user%%:*}' != 0 ] && [ '${user%%:*}' != root ]"
check "no entrypoint (got $entry)"                   "[ '$entry' = null ] || [ '$entry' = '[]' ]"

# The platform's container settings, minus the mounts: the verb must start and exit 0.
if dk run --rm --network=none --read-only --user 65534:65534 --cap-drop=ALL \
     --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
     --pids-limit 256 --ulimit nofile=1024:1024 --ulimit nproc=256:256 \
     "$DEV_TAG" forecast --help >/dev/null; then
  printf '  ok    %s\n' "'forecast --help' exits 0 under platform settings"
else
  printf '  FAIL  %s\n' "'forecast --help' under platform settings"; fail=1
fi
versions="$(dk run --rm --network=none "$DEV_TAG" python -c \
  'import importlib.metadata as m; print(" ".join(f"{p}=={m.version(p)}" for p in ("numpy","pandas","pyarrow","qfbench2-common")))')"
printf '  info  in-image versions: %s\n' "$versions"
[ "$fail" = 0 ] || die "image self-check failed"

state_put image.env \
  "LOCAL_IMAGE=$CONTENT_TAG" \
  "IMAGE_ID=$id" \
  "GIT_REV=${rev}${dirty}" \
  "SIZE_BYTES=$size" \
  "BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

info "built $CONTENT_TAG"
printf '  image id   %s\n  size       %s MB (uncompressed)\n' "$id" "$(( size / 1000000 ))"
printf '  next       bash t2-work/pack/local_run.sh --sample   (3 units, platform settings)\n'
