# Agenthon 2026 Track 3 (Accelerated Market Simulation) — team Jin & Pei: jpsim.
#
# The organizer-pinned ABIDES engine (jpmorganchase/abides-jpmc-public @ f9cbe51, BSD-3) with the
# four Track 3 patches applied, vendored under engine/ and accelerated without changing a single
# output byte (see t3-work/PLAN.md and engine/jpsim/cli.py). Runtime stack: Python 3.11 (the
# baseline's own interpreter), numpy 1.26.4 (the baseline's RNG stream), pyarrow 15.0.2 (the
# baseline's parquet writer). No pandas, scipy or coloredlogs: nothing on the run path needs them.
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

# Stage 1: compile the C++ kernel (engine/jpkernel/jpkernel.cpp) against the SAME glibc the final
# image runs (python:3.11-slim-bookworm = Debian bookworm, glibc 2.36): the reference's numpy and
# CPython call this glibc's log/exp/pow, and the kernel must call the very same functions.
FROM --platform=linux/amd64 python:3.11-slim-bookworm AS kernel-build
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY engine/jpkernel /src/jpkernel
RUN python /src/jpkernel/build.py --out /src/jpkernel/libjpkernel.so \
 && test -s /src/jpkernel/libjpkernel.so

FROM --platform=linux/amd64 python:3.11-slim-bookworm

LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="simulation"
LABEL org.opencontainers.image.source="https://github.com/dapeipeipeipei/agenthon"
LABEL org.opencontainers.image.description="Agenthon 2026 Track 3 simulator (jpsim): pinned ABIDES baseline, accelerated, output-identical"

ENV PYTHONDONTWRITEBYTECODE=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    PYTHONPATH=/opt/jpsim \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

RUN pip install --no-cache-dir "numpy==1.26.4" "pyarrow==15.0.2"

COPY engine /opt/jpsim
COPY --from=kernel-build /src/jpkernel/libjpkernel.so /opt/jpsim/jpkernel/libjpkernel.so
COPY bin/simulate /usr/local/bin/simulate
COPY bin/simulate-batch /usr/local/bin/simulate-batch

# Pre-compile every module (ours and site-packages) so the first run pays no .pyc cost: the
# root filesystem is read-only at evaluation time, so nothing could be cached then anyway.
# The last line fails the build if the C++ kernel does not load: the image must never fall back
# to the Python engine silently.
RUN chmod 0755 /usr/local/bin/simulate /usr/local/bin/simulate-batch \
 && find /opt/jpsim -type d -exec chmod 0755 {} + \
 && find /opt/jpsim -type f -exec chmod 0644 {} + \
 && chmod 0755 /opt/jpsim/jpkernel/libjpkernel.so \
 && python -m compileall -q -j 0 -o 0 -o 1 /opt/jpsim /usr/local/lib/python3.11/site-packages/numpy /usr/local/lib/python3.11/site-packages/pyarrow \
 && python -c "import sys; sys.modules['pandas']=None; import jpsim.cli, abides_core.abides, jpsim.config, jpsim.trace_fast; print('import ok, no pandas')" \
 && python -c "from jpsim import kernel; assert kernel.available(), kernel.load_error(); print('jpkernel ok')"

WORKDIR /tmp
USER 65534:65534

# No ENTRYPOINT: the harness passes the verb (`simulate ...` / `simulate-batch ...`) as the
# command and it resolves from PATH. No VOLUME anywhere in this image or its base.
CMD ["simulate", "--help"]
