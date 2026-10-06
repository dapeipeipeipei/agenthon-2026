# syntax=docker/dockerfile:1
#
# Agenthon 2026 Track 2 (Forecasting) -- team Jin & Pei submission image.
#
# Contract (track2-forecasting-public/SUBMISSION_CLI.md, Agenthon2026-public docs at v2.6.0):
#   * linux/amd64, identified by its registry digest;
#   * LABEL qfbench2.interface_version="2.0" (must equal submission.json interface_version);
#   * the verb `forecast` resolves on PATH, no ENTRYPOINT;
#   * runs as an arbitrary non-root uid (the platform uses --user 65534:65534) with a READ-ONLY
#     root filesystem, a 64 MiB noexec /tmp, 256 PIDs and 1,024 open files per process;
#   * fetches nothing at run time: every dependency is baked in. The only network use is the
#     OPTIONAL House-model call (stdlib urllib through the platform's proxy env, when the harness
#     injects MODEL_ENDPOINT/MODEL_NAME/MODEL_TOKEN); with --network=none it is skipped.
#
# Build / run: see t2-work/pack/README.md (build.sh, local_run.sh). Short form:
#   docker buildx build --platform linux/amd64 --provenance=false --load -t jinpei-t2:dev -f t2-work/Dockerfile t2-work
#   docker run --rm --network=none -v <unit>:/input:ro -v <out>:/output jinpei-t2:dev \
#     forecast --panels /input/panels/ --text /input/text/ --asof <card data_cutoff> --out /output/forecast.parquet

# Base pinned by digest (manifest list of python:3.13.16-slim-bookworm, resolved 2026-10-06), so a
# rebuild cannot silently move the interpreter. Build with --platform linux/amd64.
FROM python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

LABEL qfbench2.interface_version="2.0" \
      qfbench2.track="forecasting" \
      qfbench2.verb="forecast" \
      org.opencontainers.image.title="agenthon2026-t2-forecast" \
      org.opencontainers.image.description="Agenthon 2026 Track 2 forecaster (team Jin & Pei): block-bootstrap engine, optional House-model call"

# Runtime environment.
#   HOME=/tmp: the platform's uid 65534 has no home and / is read-only.
#   Thread caps: the engine is single-threaded numpy/pandas (no BLAS-heavy code), and --cpus is a
#   quota, not a cpuset, so an uncapped OpenBLAS/Arrow pool would size itself to the HOST's core
#   count and could run into the 256-PID limit. One compute thread each; Arrow I/O pool of 2.
#   PYTHONHASHSEED=0: set/dict-of-str iteration order is fixed, so reruns are byte-identical.
ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONPATH=/opt/t2 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/.mpl \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    ARROW_IO_THREADS=2

# Dependencies: exactly t2-work/requirements.lock (the versions the engine is tested under
# locally, see t2-work/pack/README.md). Binary wheels only -- no compiler in the image.
# The shared toolkit qfbench2-common is pinned to the v2.6.0 TARBALL (slim has no git) and
# installed --no-deps: the engine does not import it, and its full dependency set would add scipy
# (~100 MB) to an image whose cold pull counts against the unit clock. It is kept for provenance
# (`python -c "import importlib.metadata as m; print(m.version('qfbench2-common'))"` -> 2.6.0).
# Test suites and C/C++ headers of the wheels are removed, then pip itself, to shrink the image.
COPY requirements.lock /tmp/requirements.lock
RUN set -eux; \
    python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.lock; \
    python -m pip install --no-cache-dir --no-deps \
        "qfbench2-common @ https://github.com/Agenthon-2026/Agenthon2026-public/archive/refs/tags/v2.6.0.tar.gz#subdirectory=common"; \
    site="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    rm -rf "$site/pandas/tests" "$site/numpy/tests" "$site/numpy/_core/tests" \
           "$site/pyarrow/tests" "$site/pyarrow/include" "$site/pyarrow/src" "$site/numpy/_core/include"; \
    find "$site" -depth -type d -name tests -path '*/numpy/*' -exec rm -rf {} +; \
    find "$site/pyarrow" -name '*.pxd' -delete -o -name '*.pyx' -delete; \
    python -m pip uninstall -y pip; \
    rm -f /tmp/requirements.lock; \
    python -c "import numpy, pandas, pyarrow, pyarrow.parquet, qfbench2_common; print('deps ok', numpy.__version__, pandas.__version__, pyarrow.__version__)"

# The engine, byte-compiled at build time (the root filesystem is read-only at run time, so
# nothing could cache .pyc files then), readable by any uid.
COPY engine /opt/t2/engine
RUN set -eux; \
    python -m compileall -q /opt/t2/engine; \
    chmod -R a+rX /opt/t2; \
    printf '#!/bin/sh\nexec /usr/local/bin/python3 -m engine.forecast "$@"\n' > /usr/local/bin/forecast; \
    chmod 0755 /usr/local/bin/forecast

WORKDIR /opt/t2
# Same uid the platform forces; a numeric id needs no /etc/passwd entry.
USER 65534:65534

CMD ["forecast", "--help"]
