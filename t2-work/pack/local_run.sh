#!/usr/bin/env bash
# Run the submission image on Track 2 units EXACTLY the way the platform does, time every unit,
# then check every output folder with the official gates (pack/check_outputs.py).
#
#   bash t2-work/pack/local_run.sh --sample                 # 4 representative units: F1 (monthly), F2, F3, F4
#   bash t2-work/pack/local_run.sh --unit t2-F4-gbp-brexit-2016 [--unit ...]
#   bash t2-work/pack/local_run.sh --match F2               # every unit whose id contains "F2"
#   bash t2-work/pack/local_run.sh --all                    # all units (104)
# Options:
#   --image REF      image to run (default: LOCAL_IMAGE from pack/state/image.env, else jinpei-t2:dev)
#   --cpus N         default: min(16, host cores)     (platform: 16)
#   --memory SIZE    default: 128g                    (platform: 128 GiB, swap off)
#   --seed N         QFBENCH_SEED (default 0)
#   --layout staged|raw   staged (default): copy the unit and move its root *.parquet into panels/,
#                    as the organizer's staging does; raw: mount the unit folder unchanged
#   --runs-root DIR  where run folders go (default: %LOCALAPPDATA%/agenthon-t2-runs on Windows,
#                    t2-work/pack/runs elsewhere)
#   --timeout SEC    per-unit wall clock (default 1800, the platform fallback)
#   --run-name NAME  run folder name (default: a timestamp)
#   --compare DIR    also compare every forecast.parquet with DIR/<unit>/forecast.parquet
#   --native         run the engine with the pinned venv Python instead of the image (same staged
#                    inputs, argv, seed and thread env; no container limits) -- for parity checks
#                    and for machines without Docker
#   --no-check       skip the gate check at the end
#   --dry-run        print the docker commands; Docker not needed
#
# Platform settings applied (Agenthon2026-public/docs/DEVELOPMENT-RUNTIME.md, "Run it locally the
# way the platform runs it"): read-only root fs, uid 65534, no capabilities, no-new-privileges,
# 64 MiB noexec /tmp, 256 PIDs, 1,024 fds, nproc 256, CPU/memory quota with swap off,
# --network=none, unit at /input read-only, /output read-write, QFBENCH_SEED, and the argv
#   forecast --panels /input/panels/ --text /input/text/ --asof <card data_cutoff> --out /output/forecast.parquet
source "$(dirname "$0")/common.sh"

image=""; cpus=""; memory="128g"; seed=0; layout=staged; runs_root=""; tmo=1800
check=1; dry=0; units=(); match=""; all=0; sample=0; native=0; run_name=""; compare_dir=""
SAMPLE_UNITS=(t2-F1-cpi-glidepath-2023 t2-F2-abenomics-2012 t2-F3-bear-flattener-2022 t2-F4-gbp-brexit-2016)

while [ $# -gt 0 ]; do
  case "$1" in
    --image) image="$2"; shift ;;
    --cpus) cpus="$2"; shift ;;
    --memory) memory="$2"; shift ;;
    --seed) seed="$2"; shift ;;
    --layout) layout="$2"; shift ;;
    --runs-root) runs_root="$2"; shift ;;
    --timeout) tmo="$2"; shift ;;
    --unit) units+=("$2"); shift ;;
    --match) match="$2"; shift ;;
    --all) all=1 ;;
    --sample) sample=1 ;;
    --no-check) check=0 ;;
    --dry-run) dry=1 ;;
    --native) native=1 ;;
    --run-name) run_name="$2"; shift ;;
    --compare) compare_dir="$2"; shift ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done
case "$layout" in staged|raw) ;; *) die "--layout must be staged or raw" ;; esac
[[ "$seed" =~ ^[0-9]+$ ]] || die "--seed must be a non-negative integer"

# ---------------------------------------------------------------- which units
[ -d "$UNITS_DIR" ] || die "units not found at $UNITS_DIR (clone track2-forecasting-public next to t2-work's parent)"
if [ "$sample" = 1 ]; then
  for u in "${SAMPLE_UNITS[@]}"; do
    if [ -d "$UNITS_DIR/$u" ]; then units+=("$u"); else
      # a sampled id can disappear upstream: substitute the first unit of that family
      fam="${u:0:5}"; sub="$(cd "$UNITS_DIR" && ls -d "${fam}"* 2>/dev/null | head -n 1)"
      [ -n "$sub" ] && units+=("$sub")
    fi
  done
fi
if [ "$all" = 1 ] || [ -n "$match" ]; then
  while IFS= read -r u; do units+=("$u"); done < <(cd "$UNITS_DIR" && for d in */; do d="${d%/}"; [[ "$d" == *"$match"* ]] && [ -f "$d/card.toml" ] && echo "$d"; done)
fi
[ "${#units[@]}" -gt 0 ] || die "no units selected (use --sample, --unit, --match or --all)"

# ---------------------------------------------------------------- image, resources, paths
if [ -z "$image" ]; then image="$(state_get image.env LOCAL_IMAGE || true)"; fi
[ -n "$image" ] || image="$LOCAL_REPO:dev"
if [ -z "$cpus" ]; then
  n="$(nproc 2>/dev/null || echo 4)"; cpus=$(( n < 16 ? n : 16 ))
fi
if [ -z "$runs_root" ]; then
  if [ -n "${LOCALAPPDATA:-}" ]; then
    # ASCII-only path: keeps Docker Desktop bind mounts away from the non-ASCII project path
    runs_root="$(cygpath -m "$LOCALAPPDATA" 2>/dev/null || printf '%s' "$LOCALAPPDATA")/agenthon-t2-runs"
  else
    runs_root="$PACK_DIR/runs"
  fi
fi
run_id="${run_name:-$(date +%Y%m%d-%H%M%S)}"
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "--run-name may only use letters, digits, '.', '_' and '-'"
RUN_DIR="$runs_root/$run_id"

PY="$(pick_python)"
# unit<TAB>asof for every selected unit, read from card.toml with a real TOML parser
asof_table="$("$PY" - "$UNITS_DIR" "${units[@]}" <<'EOF'
import sys, tomllib, pathlib
root = pathlib.Path(sys.argv[1])
for u in sys.argv[2:]:
    card = tomllib.loads((root / u / "card.toml").read_text(encoding="utf-8"))
    asof = card.get("provenance", {}).get("data_cutoff") or card.get("text", {}).get("cutoff")
    if not asof:
        sys.exit(f"{u}: card.toml has no [provenance].data_cutoff")
    print(f"{u}\t{asof}")
EOF
)"

mode="image $image"; [ "$native" = 1 ] && mode="NATIVE (venv $PY, no container)"
info "$mode | ${#units[@]} unit(s) | cpus=$cpus memory=$memory seed=$seed layout=$layout"
info "run folder $RUN_DIR"

docker_args() {   # docker_args <input dir> <output dir>
  printf '%s\n' run --rm --name "$cname" \
    --read-only --user 65534:65534 --cap-drop=ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
    --pids-limit 256 --ulimit nofile=1024:1024 --ulimit nproc=256:256 \
    --cpus "$cpus" --memory "$memory" --memory-swap "$memory" \
    --network=none \
    -v "$1:/input:ro" -v "$2:/output" \
    -e "QFBENCH_SEED=$seed" -e QFBENCH_NETWORK=none
}

if [ "$dry" = 0 ]; then
  [ -e "$RUN_DIR" ] && die "run folder already exists: $RUN_DIR"
  if [ "$native" = 0 ]; then
    require_docker
    dk image inspect "$image" >/dev/null 2>&1 || die "image $image not found locally; run build.sh first"
  fi
  mkdir -p "$RUN_DIR/in" "$RUN_DIR/out" "$RUN_DIR/logs"
  printf 'unit\tasof\texit_code\tseconds\n' > "$RUN_DIR/timings.tsv"
  if [ "$native" = 1 ]; then
    { echo "mode=native"; echo "python=$PY"; echo "seed=$seed"; echo "layout=$layout"; } > "$RUN_DIR/run.env"
  else
    { echo "image=$image"; echo "image_id=$(dk image inspect --format '{{.Id}}' "$image")";
      echo "cpus=$cpus"; echo "memory=$memory"; echo "seed=$seed"; echo "layout=$layout"; } > "$RUN_DIR/run.env"
  fi
fi

n_fail=0; i=0
while IFS=$'\t' read -r unit asof; do
  i=$((i + 1))
  asof="${asof%$'\r'}"   # Windows Python prints CRLF into the pipe
  src="$UNITS_DIR/$unit"
  if [ "$layout" = raw ]; then in_dir="$src"; else in_dir="$RUN_DIR/in/$unit"; fi
  out_dir="$RUN_DIR/out/$unit"
  cname="t2run-$$-$i"
  mapfile -t args < <(docker_args "$in_dir" "$out_dir")
  verb=(forecast --panels /input/panels/ --text /input/text/ --asof "$asof" --out /output/forecast.parquet)
  if [ "$dry" = 1 ]; then
    printf '[%d/%d] docker' "$i" "${#units[@]}"; printf ' %q' "${args[@]}" "$image" "${verb[@]}"; printf '\n'
    continue
  fi
  if [ "$layout" = staged ]; then
    # Staged layout: the unit's root-level panel parquets move into panels/; everything else as is.
    mkdir -p "$in_dir/panels"
    for f in "$src"/* "$src"/.[!.]*; do
      [ -e "$f" ] || continue
      b="$(basename "$f")"
      if [ -f "$f" ] && [[ "$b" == *.parquet ]]; then cp -p "$f" "$in_dir/panels/"
      elif [ "$b" = panels ] && [ -d "$f" ]; then cp -pR "$f/." "$in_dir/panels/"
      else cp -pR "$f" "$in_dir/"; fi
    done
    chmod -R a+rX "$in_dir"
  fi
  mkdir -p "$out_dir"; chmod 777 "$out_dir"
  t0="$(date +%s.%N)"
  rc=0
  if [ "$native" = 1 ]; then
    # Same argv as the container, pointed at the staged copy; the env the image sets.
    (cd "$T2_DIR" && QFBENCH_SEED="$seed" QFBENCH_NETWORK=none PYTHONHASHSEED=0 \
       OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 ARROW_IO_THREADS=2 \
       timeout --kill-after=30 "$tmo" "$PY" -m engine.forecast --panels "$in_dir/panels/" \
         --text "$in_dir/text/" --asof "$asof" --out "$out_dir/forecast.parquet") \
      < /dev/null > "$RUN_DIR/logs/$unit.log" 2>&1 || rc=$?
  else
    timeout --kill-after=30 "$tmo" env MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
      docker "${args[@]}" "$image" "${verb[@]}" < /dev/null > "$RUN_DIR/logs/$unit.log" 2>&1 || rc=$?
  fi
  t1="$(date +%s.%N)"
  if [ "$native" = 0 ] && { [ "$rc" = 124 ] || [ "$rc" = 137 ]; }; then dk rm -f "$cname" >/dev/null 2>&1 || true; fi
  secs="$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.2f", b - a }')"
  printf '%s\t%s\t%s\t%s\n' "$unit" "$asof" "$rc" "$secs" >> "$RUN_DIR/timings.tsv"
  status=ok; [ "$rc" = 0 ] || { status="EXIT $rc"; n_fail=$((n_fail + 1)); }
  printf '[%3d/%d] %-8s %-45s %7ss  asof %s\n' "$i" "${#units[@]}" "$status" "$unit" "$secs" "$asof"
  if [ "$rc" != 0 ]; then tail -n 5 "$RUN_DIR/logs/$unit.log" | sed 's/^/          | /'; fi
done <<< "$asof_table"

[ "$dry" = 1 ] && exit 0
# staged inputs are copies; drop them so a run folder only keeps outputs, logs and timings
[ "$layout" = staged ] && rm -rf "$RUN_DIR/in"

info "process exits: $(( ${#units[@]} - n_fail ))/${#units[@]} exited 0"
info "timings: $RUN_DIR/timings.tsv"
if [ "$check" = 1 ]; then
  info "checking outputs (output-tree rules + official gates g0-g3) with $PY"
  "$PY" "$PACK_DIR/check_outputs.py" --out-root "$RUN_DIR/out" --timings "$RUN_DIR/timings.tsv" \
        --summary "$RUN_DIR/summary.json" ${compare_dir:+--compare "$compare_dir"}
fi
