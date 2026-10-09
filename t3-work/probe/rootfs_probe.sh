#!/usr/bin/env bash
# Measure what a runtime hook that runs ldconfig against the container rootfs (the NVIDIA
# container toolkit's ldcache update) would face in an image: file / .so counts, ld.so.conf
# directories, and the wall time of `ldconfig -r <rootfs>` (cache rebuild), plus the same with
# an extra driver-library directory argument as libnvidia-container passes.
#   rootfs_probe.sh <image> <name> <reps>
set -euo pipefail
img=$1; name=$2; reps=${3:-20}
d=$(mktemp -d /tmp/rootfs-XXXX)
cid=$(docker create "$img")
docker export "$cid" | sudo tar -x -C "$d"
docker rm -f "$cid" >/dev/null
files=$(sudo find "$d" -xdev -type f | wc -l)
inodes=$(sudo find "$d" -xdev | wc -l)
sos=$(sudo find "$d" -xdev -type f \( -name '*.so' -o -name '*.so.*' \) | wc -l)
size=$(sudo du -sb "$d" | cut -f1)
confdirs=$( (sudo sh -c "cat $d/etc/ld.so.conf $d/etc/ld.so.conf.d/*.conf" 2>/dev/null || true) | grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*include' | grep -c . || true)
cache_before=$(sudo stat -c %s "$d/etc/ld.so.cache" 2>/dev/null || echo 0)
sudo mkdir -p "$d/etc" "$d/usr/lib/x86_64-linux-gnu"
sudo ldconfig -r "$d" 2>/dev/null || true
entries=$(sudo ldconfig -r "$d" -p 2>/dev/null | head -1 | grep -o '[0-9]*' | head -1 || true)
python3 - "$d" "$reps" "$name" "$files" "$inodes" "$sos" "$size" "$confdirs" "$cache_before" "${entries:-0}" <<'PY'
import subprocess, sys, time, statistics
d, reps, name = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def t(cmd, n, cold=False):
    xs = []
    for _ in range(n):
        if cold:
            subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"])
        a = time.perf_counter()
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        xs.append(time.perf_counter() - a)
    return statistics.median(xs) * 1e3
noop = t(["sudo", "true"], reps)
warm = t(["sudo", "ldconfig", "-r", d], reps)
drv = t(["sudo", "ldconfig", "-r", d, "/usr/lib/x86_64-linux-gnu"], reps)
cold = t(["sudo", "ldconfig", "-r", d], max(3, reps // 4), cold=True)
print(f"{name:7s} files={sys.argv[4]:>6s} inodes={sys.argv[5]:>6s} so={sys.argv[6]:>5s} bytes={int(sys.argv[7]):>11d} "
      f"ldconf_dirs={sys.argv[8]} img_cache={sys.argv[9]}B cache_entries={sys.argv[10]} | "
      f"ldconfig ms (minus sudo {noop:.1f}): warm={warm - noop:.1f} +drvdir={drv - noop:.1f} cold={cold - noop:.1f}")
PY
sudo rm -rf "$d"
