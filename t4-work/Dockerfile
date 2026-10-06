# Agenthon 2026 Track 4 (Explainability) — analyst image.
#
# The agent itself is pure standard library (agent/). The shared toolkit is installed only so the
# build-time self-test can validate the answer against the published analysis.schema.json; the
# agent never imports it at run time.
#
# Build (Docker Desktop must be running; NOT done by the assistant):
#   docker build --platform=linux/amd64 -t agenthon-t4-analyst:dev -f t4-work/Dockerfile t4-work
#
# Run one unit the way the Development platform does:
#   docker run --rm --platform=linux/amd64 \
#     --read-only --user 65534:65534 --cap-drop=ALL --security-opt no-new-privileges \
#     --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
#     --pids-limit 256 --ulimit nofile=1024:1024 --ulimit nproc=256:256 \
#     --cpus 16 --memory 128g --memory-swap 128g --network=none \
#     -v "$PWD/track4-analysis-public/units/t4-EXAMPLE-eps-beat":/input:ro -v "$PWD/out":/output \
#     -e QFBENCH_SEED=0 -e QFBENCH_NETWORK=none \
#     agenthon-t4-analyst:dev analyze --task /input/task.json --corpus /input/corpus/ --out /output/answer.json

FROM --platform=linux/amd64 python:3.13-slim-bookworm

LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="analysis"
LABEL qfbench2.verb="analyze"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    PYTHONPATH=/opt/t4 \
    T4_USE_HOUSE=0

# Pinned. Toolkit via release tarball (the slim image has no git).
RUN pip install --no-cache-dir \
        "numpy==2.2.6" \
        "scipy==1.15.3" \
        "jsonschema==4.23.0" \
        "qfbench2-common @ https://github.com/Agenthon-2026/Agenthon2026-public/archive/refs/tags/v2.6.0.tar.gz#subdirectory=common"

COPY agent /opt/t4/agent
COPY bin/analyze /usr/local/bin/analyze
RUN chmod 0755 /usr/local/bin/analyze \
 && find /opt/t4 -type d -exec chmod 0755 {} + \
 && find /opt/t4 -type f -exec chmod 0644 {} + \
 && python -m agent.selftest

WORKDIR /tmp
USER 65534:65534

# No ENTRYPOINT: the harness passes `analyze --task ... --corpus ... --out ...` as the command,
# and `analyze` resolves from PATH.
CMD ["analyze", "--help"]
