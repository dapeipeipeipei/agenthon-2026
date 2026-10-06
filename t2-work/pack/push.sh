#!/usr/bin/env bash
# Push the built image to GHCR and print its immutable digest.  OUTWARD-FACING: asks first.
#
#   bash t2-work/pack/push.sh                 # push LOCAL_IMAGE from pack/state/image.env
#   bash t2-work/pack/push.sh --dry-run       # show what would happen, push nothing
# Options:
#   --image REF    local image to push (default: LOCAL_IMAGE from build.sh, else jinpei-t2:dev)
#   --tag TAG      remote tag (default: the local content tag, e.g. a726934dbd72-1f2e3d4c5b6a)
#   --yes          do not ask for confirmation (CI)
#   --no-login     assume `docker login ghcr.io` was already done (CI); otherwise this script logs
#                  in with the token of the logged-in `gh` account, piped on stdin (never in argv),
#                  and logs out again at the end
#
# Needs: `gh auth status` showing the write:packages scope. If missing, run once:
#   gh auth refresh -h github.com -s write:packages,read:packages
# Result: pack/state/pushed.env with IMAGE_REF=ghcr.io/<owner>/<name>@sha256:...
# A new package on ghcr.io is PRIVATE. Making it public is a separate, manual step (README step 6).
source "$(dirname "$0")/common.sh"

image=""; tag=""; yes=0; login=1; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --image) image="$2"; shift ;;
    --tag) tag="$2"; shift ;;
    --yes) yes=1 ;;
    --no-login) login=0 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

if [ -z "$image" ]; then image="$(state_get image.env LOCAL_IMAGE || true)"; fi
[ -n "$image" ] || image="$LOCAL_REPO:dev"
if [ -z "$tag" ]; then
  tag="${image##*:}"
  [ "$tag" = "$image" ] && tag=latest
fi
[[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "invalid tag: $tag"
REMOTE="ghcr.io/$GHCR_REPO:$tag"

info "local image : $image"
info "push to     : $REMOTE"
case "$tag" in *-dirty*) warn "this image was built from UNCOMMITTED engine/Dockerfile changes" ;; esac

if [ "$login" = 1 ]; then
  command -v gh >/dev/null 2>&1 || die "gh CLI not found; install it or log in to ghcr.io yourself and pass --no-login"
  scopes="$(gh auth status -h github.com 2>&1 || true)"
  if ! printf '%s' "$scopes" | grep -q "write:packages"; then
    die "the gh token lacks the write:packages scope. Run once:  gh auth refresh -h github.com -s write:packages,read:packages   (a browser window opens), then rerun this script"
  fi
fi

if [ "$dry" = 1 ]; then
  info "dry run: would run  docker tag $image $REMOTE  &&  docker push $REMOTE"
  exit 0
fi

require_docker
dk image inspect "$image" >/dev/null 2>&1 || die "local image $image not found; run build.sh first"
platform="$(dk image inspect --format '{{.Os}}/{{.Architecture}}' "$image")"
[ "$platform" = linux/amd64 ] || die "image platform is $platform, must be linux/amd64"
label="$(dk image inspect --format '{{index .Config.Labels "qfbench2.interface_version"}}' "$image")"
[ "$label" = 2.0 ] || die "image label qfbench2.interface_version is '$label', must be 2.0"

if [ "$yes" != 1 ]; then
  printf 'This uploads the image to ghcr.io (as a PRIVATE package). Type "push" to continue: '
  read -r answer
  [ "$answer" = push ] || die "cancelled"
fi

logged_in_here=0
cleanup() { if [ "$logged_in_here" = 1 ]; then dk logout ghcr.io >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
if [ "$login" = 1 ]; then
  info "logging in to ghcr.io as $GHCR_OWNER (token from gh, via stdin)"
  gh auth token -h github.com | dk login ghcr.io -u "$GHCR_OWNER" --password-stdin >/dev/null \
    || die "docker login to ghcr.io failed"
  logged_in_here=1
fi

dk tag "$image" "$REMOTE"
log="$(mktemp)"
dk push "$REMOTE" 2>&1 | tee "$log"
digest="$(grep -Eo 'digest: sha256:[0-9a-f]{64}' "$log" | tail -n 1 | cut -d' ' -f2 || true)"
rm -f "$log"
if ! is_digest "${digest:-x}"; then
  digest="$(dk image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$REMOTE" \
            | grep -F "ghcr.io/$GHCR_REPO@" | head -n 1 | sed 's/.*@//' || true)"
fi
is_digest "${digest:-x}" || die "push finished but no sha256 digest could be read; check 'docker push' output above"

REF="ghcr.io/$GHCR_REPO@$digest"
state_put pushed.env \
  "IMAGE_REF=$REF" "REGISTRY=ghcr.io" "REPOSITORY=$GHCR_REPO" "DIGEST=$digest" \
  "TAG=$tag" "LOCAL_IMAGE=$image" "PUSHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  { echo "digest=$digest"; echo "image_ref=$REF"; } >> "$GITHUB_OUTPUT"
fi

echo
info "pushed. Immutable reference (this goes into submission.json):"
printf '    %s\n\n' "$REF"
printf 'Next:\n'
printf '  1. make the package public: https://github.com/users/%s/packages/container/%s/settings\n' "$GHCR_OWNER" "$GHCR_NAME"
printf '     -> "Danger Zone" -> "Change visibility" -> Public\n'
printf '  2. bash t2-work/pack/verify_anonymous_pull.sh\n'
printf '  3. bash t2-work/pack/pack.sh --team-number <N>\n'
