# Agenthon 2026 Track 3 (Accelerated Market Simulation) — team Jin & Pei: jpsim-native (lean).
#
# The submission verbs `simulate` / `simulate-batch` ARE the static binary jpsim-native (C++ kernel
# that reproduces the organizer-pinned ABIDES engine bit for bit, own parquet writer, no shared
# libraries, no Python): a container runs exec -> simulate -> write two parquet files -> exit.
# The Python engine (vendored ABIDES + patches, numpy 1.26.4 / pyarrow 15.0.2 / scipy) stays in
# the image ONLY as the fallback the binary execs for a scenario outside the kernel's reproduced
# scope (`simulate-py`, JPSIM_KERNEL=py); nothing of it is touched on the normal path.
#
# Build (linux/amd64 only; done by .github/workflows/t3-image.yml, Docker is not available on the
# development box):
#   docker buildx build --platform linux/amd64 -t jpsim:dev -f t3-work/Dockerfile t3-work
#
# Run one unit the way the platform does:
#   docker run --rm --network=none --cpus=4 --memory=16g --pids-limit 256 --read-only \
#     --user 65534:65534 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
#     -v <unit>:/input:ro -v <out>:/output jpsim:dev \
#     simulate --config /input/scenario.json --out /output/trace.parquet
#   ... jpsim:dev simulate-batch --batch-dir /input/scenarios --out-dir /output

# Stage 1: compile the kernel against the SAME glibc the final image runs (python:3.11-slim-bookworm
# = Debian bookworm, glibc 2.36): the reference's numpy and CPython call this glibc's log/exp/pow,
# and the kernel must call the very same functions. Static linking keeps that: libm.a of this very
# glibc (its FMA/non-FMA variants are still IFUNC-selected at run time, as in the shared library).
FROM --platform=linux/amd64 python:3.11-slim-bookworm AS kernel-build
RUN apt-get update && apt-get install -y --no-install-recommends g++ file && rm -rf /var/lib/apt/lists/*
COPY engine/jpkernel /src/jpkernel
# libjpkernel.so: the kernel as a shared library (ctypes binding, Python verbs: `python -m jpsim.cli`).
# jpsim-native: the static, Python-free `simulate` / `simulate-batch` binary.
RUN python /src/jpkernel/build.py --out /src/jpkernel/libjpkernel.so \
 && test -s /src/jpkernel/libjpkernel.so \
 && python /src/jpkernel/build_native.py --out /src/jpkernel/jpsim-native \
 && test -x /src/jpkernel/jpsim-native \
 && file /src/jpkernel/jpsim-native | grep -q "statically linked" \
 && /src/jpkernel/jpsim-native --help

FROM --platform=linux/amd64 python:3.11-slim-bookworm

LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="simulation"
LABEL org.opencontainers.image.source="https://github.com/dapeipeipeipei/agenthon"
LABEL org.opencontainers.image.description="Agenthon 2026 Track 3 simulator (jpsim-native): pinned ABIDES baseline reproduced in C++, static binary, output-identical"

ENV PYTHONDONTWRITEBYTECODE=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    PYTHONPATH=/opt/jpsim \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

# Fallback engine only (never on the normal path): numpy / pyarrow of the baseline, scipy for the
# ABIDES line-distance latency model (scenario without latency_config).
RUN pip install --no-cache-dir "numpy==1.26.4" "pyarrow==15.0.2" "scipy==1.17.1"

COPY engine /opt/jpsim
COPY --from=kernel-build /src/jpkernel/libjpkernel.so /opt/jpsim/jpkernel/libjpkernel.so
COPY bin/simulate-py /usr/local/bin/simulate-py
COPY bin/simulate-batch-py /usr/local/bin/simulate-batch-py

# Pre-compile every module (ours and site-packages) so the Python fallback pays no .pyc cost (the
# root filesystem is read-only at evaluation time); the import lines fail the build if the fallback
# engine or the ctypes kernel does not load.
RUN chmod 0755 /usr/local/bin/simulate-py /usr/local/bin/simulate-batch-py \
 && find /opt/jpsim -type d -exec chmod 0755 {} + \
 && find /opt/jpsim -type f -exec chmod 0644 {} + \
 && chmod 0755 /opt/jpsim/jpkernel/libjpkernel.so \
 && python -m compileall -q -j 0 -o 0 -o 1 /opt/jpsim /usr/local/lib/python3.11/site-packages/numpy /usr/local/lib/python3.11/site-packages/pyarrow \
 && python -c "import jpsim.cli, abides_core.abides, jpsim.config, jpsim.trace_fast; import scipy.spatial.distance; print('import ok')" \
 && python -c "from jpsim import kernel; assert kernel.available(), kernel.load_error(); print('jpkernel ok')"

# The verbs: the static binary itself, under both names (it takes the verb from its program name).
# Last layer, so the hot path's only file is at the top of the overlay.
COPY --from=kernel-build --chmod=0755 /src/jpkernel/jpsim-native /usr/local/bin/simulate
COPY --from=kernel-build --chmod=0755 /src/jpkernel/jpsim-native /usr/local/bin/simulate-batch
RUN /usr/local/bin/simulate --help | head -1 && /usr/local/bin/simulate-batch --help | head -1 \
 && (ldd /usr/local/bin/simulate 2>&1 | grep -q "not a dynamic executable")

WORKDIR /tmp
USER 65534:65534

# No ENTRYPOINT: the harness passes the verb (`simulate ...` / `simulate-batch ...`) as the
# command and it resolves from PATH. No VOLUME anywhere in this image or its base.
CMD ["simulate", "--help"]
