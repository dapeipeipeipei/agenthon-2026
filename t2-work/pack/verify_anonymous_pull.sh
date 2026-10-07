#!/usr/bin/env bash
# Prove the image can be pulled by digest WITHOUT any of our credentials.
# Follows Agenthon2026-public/starter-packs/track2/RUNTIME-ENVIRONMENT.md
# ("The anonymous pullability check") exactly, then goes further: reads the manifest, checks it is
# linux/amd64 with the 2.0 label, and fetches the config and every layer anonymously.
#
#   bash t2-work/pack/verify_anonymous_pull.sh                          # digest from pack/state/pushed.env
#   bash t2-work/pack/verify_anonymous_pull.sh --digest sha256:<64 hex> [--repo owner/name]
#   ... --docker-pull     also `docker pull` it with an EMPTY docker config (no saved logins) and
#                         run `forecast --help` (needs a running Docker engine)
#
# Exit 0 = anonymous access works for that exact digest. Needs only curl and Python.
# Nothing here sends a token of ours: the only token used is the anonymous one GHCR hands out.
source "$(dirname "$0")/common.sh"

repo=""; digest=""; docker_pull=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="$2"; shift ;;
    --digest) digest="$2"; shift ;;
    --docker-pull) docker_pull=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
[ -n "$repo" ] || repo="$(state_get pushed.env REPOSITORY || true)"
[ -n "$repo" ] || repo="$GHCR_REPO"
[ -n "$digest" ] || digest="$(state_get pushed.env DIGEST || true)"
[ -n "$digest" ] || die "no digest: pass --digest sha256:<64 hex> (printed by push.sh / the CI run summary)"
is_digest "$digest" || die "not a digest: $digest"
[[ "$repo" =~ ^[a-z0-9._/-]+$ ]] || die "repository must be lowercase owner/name: $repo"

# Project venv (.venv-docker, else .venv), else any python3/python on PATH. find_python never
# calls die, so a missing venv falls through to PATH instead of exiting silently with status 1.
PY="$(find_python || command -v python3 || command -v python || true)"
[ -n "$PY" ] || die "no Python found: create the repo .venv (or run t2-work/pack/make_venv.sh), or put python3 on PATH"
CURL=(curl -q -sS --proto '=https' --max-time 60)   # -q: ignore ~/.curlrc; curl never reads .netrc unless asked
ACCEPT_ALL='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fail=0
ok()  { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }

info "anonymous check of ghcr.io/$repo@$digest"

# 1. Anonymous pull token (no Authorization header of ours).
"${CURL[@]}" "https://ghcr.io/token?scope=repository:$repo:pull&service=ghcr.io" > "$tmp/token.json" || die "token request failed (network?)"
TOKEN="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$tmp/token.json" 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "  FAIL  GHCR refused an anonymous token: $(head -c 300 "$tmp/token.json")"
  echo "        -> the package ghcr.io/$repo is PRIVATE (or does not exist). Make it public (README step 6) and rerun."
  exit 1
fi

# 2. The documented check, verbatim: OCI index Accept header, expect 200.
code_doc="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  "https://ghcr.io/v2/$repo/manifests/$digest")"
# 3. Same request accepting every manifest type (what `docker pull` sends).
code_all="$("${CURL[@]}" -o "$tmp/manifest.json" -D "$tmp/headers.txt" -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" -H "Accept: $ACCEPT_ALL" \
  "https://ghcr.io/v2/$repo/manifests/$digest")"
printf '  info  documented check (OCI index Accept): HTTP %s\n' "$code_doc"
printf '  info  full Accept list:                    HTTP %s\n' "$code_all"
if [ "$code_all" != 200 ]; then
  bad "manifest not anonymously readable (HTTP $code_all)."
  case "$code_all" in
    401|403) echo "        -> the package is still PRIVATE. Make it public (README step 6) and rerun." ;;
    404)     echo "        -> no such digest in ghcr.io/$repo. Check the digest/repository." ;;
  esac
  exit 1
fi
[ "$code_doc" = 200 ] && ok "documented anonymous manifest check returns 200" \
  || printf '  info  the documented one-header check gave %s: the image is a single-platform manifest, not an index; the full-Accept check above is what a pull uses\n' "$code_doc"

# 4. Walk the manifest: pick linux/amd64, check the config, fetch every blob anonymously.
"$PY" - "$tmp" "$repo" "$digest" "$TOKEN" "$ACCEPT_ALL" <<'EOF' || fail=1
import hashlib, json, sys, urllib.request
tmp, repo, digest, token, accept = sys.argv[1:6]
base = f"https://ghcr.io/v2/{repo}"

class NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    # GHCR redirects blobs to a storage host: never forward the bearer there.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop("Authorization", None); new.unredirected_hdrs.pop("Authorization", None)
        return new
opener = urllib.request.build_opener(NoAuthOnRedirect)

def get(url, accept_hdr=None, head=False):
    req = urllib.request.Request(url, method="HEAD" if head else "GET")
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    if accept_hdr:
        req.add_header("Accept", accept_hdr)
    with opener.open(req, timeout=120) as r:
        return r.status, (b"" if head else r.read())

ok = True
def say(good, msg):
    global ok
    print(f"  {'ok  ' if good else 'FAIL'}  {msg}")
    ok = ok and good

body = open(f"{tmp}/manifest.json", "rb").read()
say("sha256:" + hashlib.sha256(body).hexdigest() == digest, "manifest bytes hash to the requested digest")
m = json.loads(body)
if "manifests" in m:  # index / manifest list
    amd = [d for d in m["manifests"] if d.get("platform", {}).get("os") == "linux"
           and d.get("platform", {}).get("architecture") == "amd64"]
    say(bool(amd), f"index lists a linux/amd64 image ({len(m['manifests'])} entries)")
    if not amd:
        sys.exit(1)
    _, body = get(f"{base}/manifests/{amd[0]['digest']}", accept)
    m = json.loads(body)
_, cfg_raw = get(f"{base}/blobs/{m['config']['digest']}")
cfg = json.loads(cfg_raw)
say(cfg.get("os") == "linux" and cfg.get("architecture") == "amd64",
    f"config platform {cfg.get('os')}/{cfg.get('architecture')}")
c = cfg.get("config", {})
say((c.get("Labels") or {}).get("qfbench2.interface_version") == "2.0", "label qfbench2.interface_version=2.0")
user = c.get("User") or ""
say(user not in ("", "0", "root") and not user.startswith(("0:", "root:")), f"non-root user ({user or 'unset'})")
say(not c.get("Entrypoint"), f"no entrypoint (Cmd {c.get('Cmd')})")
total = 0
for i, layer in enumerate(m.get("layers", []), 1):
    try:
        status, _ = get(f"{base}/blobs/{layer['digest']}", head=True)
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "code", str(e))
    total += int(layer.get("size", 0))
    say(status == 200, f"layer {i}/{len(m['layers'])} {layer['digest'][:19]} {int(layer.get('size', 0))/1e6:.1f} MB anonymously fetchable (HTTP {status})")
print(f"  info  compressed image size (sum of layers): {total/1e6:.1f} MB")
sys.exit(0 if ok else 1)
EOF

# 5. Optional: a real pull with an empty docker config, so no saved login can help.
if [ "$docker_pull" = 1 ]; then
  require_docker
  empty_cfg="$tmp/docker-config"; mkdir -p "$empty_cfg"; echo '{}' > "$empty_cfg/config.json"
  ref="ghcr.io/$repo@$digest"
  if DOCKER_CONFIG="$empty_cfg" dk pull --platform linux/amd64 "$ref" >/dev/null 2>&1; then
    ok "docker pull with an empty config (no credentials)"
    if dk run --rm --network=none --read-only --user 65534:65534 "$ref" forecast --help >/dev/null 2>&1; then
      ok "pulled image runs 'forecast --help'"
    else bad "pulled image failed 'forecast --help'"; fi
  else
    bad "docker pull with an empty config failed"
  fi
fi

echo
if [ "$fail" = 0 ]; then
  info "PASS: ghcr.io/$repo@$digest is anonymously pullable"
else
  info "FAILED: see the FAIL lines above"; exit 1
fi
