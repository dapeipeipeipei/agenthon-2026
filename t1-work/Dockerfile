# Agenthon 2026 Track 1 (Coding) — agent image, team Jin & Pei.
#
# The agent (agent/) is standard library only; the image carries the numeric stack the generated
# scripts run on (requirements.txt, pinned). No ENTRYPOINT: the harness passes
# `solve --task-dir /input --out /app/output` as the command and `solve` resolves from PATH.
#
# Platform facts baked in: non-root user, read-only root filesystem (HOME and every cache under
# /tmp, a 64 MiB noexec tmpfs on the platform), `/app/data` is a symlink to the mounted unit's
# data directory so the instruction's `/app/data/<file>` resolves without any copy.
#
# Build (CI does this; local Docker is not required):
#   docker buildx build --platform linux/amd64 -t jinpei-t1:dev -f t1-work/Dockerfile t1-work
#
# Run one unit the way the Development platform does (plus a mock House on the host):
#   docker run --rm --platform=linux/amd64 \
#     --read-only --user 65534:65534 --cap-drop=ALL --security-opt no-new-privileges \
#     --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
#     --pids-limit 256 --ulimit nofile=1024:1024 --ulimit nproc=256:256 \
#     --cpus 16 --memory 128g --memory-swap 128g --network=none \
#     -v "$PWD/track1-coding-public/units/t1-zero-coupon-bootstrapping":/input:ro \
#     -v "$PWD/out":/app/output -v "$PWD/out":/output \
#     -e QFBENCH_SEED=0 -e QFBENCH_NETWORK=none \
#     jinpei-t1:dev solve --task-dir /input --out /app/output

FROM --platform=linux/amd64 python:3.13-slim-bookworm

LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="coding"
LABEL qfbench2.verb="solve"
LABEL org.opencontainers.image.source="https://github.com/dapeipeipeipei/agenthon-2026"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/xdg \
    MPLCONFIGDIR=/tmp/mpl \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLBACKEND=Agg \
    PYTHONPATH=/opt/jp

COPY requirements.txt /opt/jp/requirements.txt
RUN pip install --no-cache-dir -r /opt/jp/requirements.txt \
 && python -c "import numpy, pandas, scipy, sklearn, statsmodels, pyarrow, polars, arch, numba, matplotlib, plotly, openpyxl, lxml, bs4; print('stack ok')"

# Output mount points and the /app/data convenience link (the unit is mounted read-only at /input).
RUN mkdir -p /app/output /output /input /app \
 && ln -s /input/environment/data /app/data

COPY agent /opt/jp/agent
COPY bin/solve /usr/local/bin/solve
RUN chmod 0755 /usr/local/bin/solve \
 && find /opt/jp -type d -exec chmod 0755 {} + \
 && find /opt/jp -type f -exec chmod 0644 {} + \
 && cd /opt/jp && python -m agent.selftest

WORKDIR /tmp
USER 65534:65534

# No ENTRYPOINT (see above). A bare `docker run <image>` just prints the usage.
CMD ["solve", "--help"]
