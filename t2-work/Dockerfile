# Agenthon 2026 Track 2 — team Jin & Pei submission image.
# Shape mirrors the track's reference Dockerfile: verb on PATH, non-root, offline.
#
#   docker build --platform=linux/amd64 -t jinpei-t2:dev -f t2-work/Dockerfile t2-work
#   docker run --rm --network=none --cpus=4 --memory=16g \
#     -v <unit>:/input:ro -v <out>:/output jinpei-t2:dev \
#     forecast --panels /input --text /input/text --asof 2016-05-31 --out /output/forecast.parquet

FROM python:3.13-slim-bookworm

LABEL qfbench2.interface_version=2.0
LABEL qfbench2.track=forecasting
LABEL qfbench2.verb=forecast

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Pinned. Toolkit via tarball (slim image has no git). Versions match the reference image.
RUN pip install --no-cache-dir \
        "numpy==2.1.3" \
        "pandas==2.2.3" \
        "pyarrow==18.1.0" \
        "jsonschema==4.23.0" \
        "qfbench2-common @ https://github.com/Agenthon-2026/Agenthon2026-public/archive/refs/tags/v2.3.1.tar.gz#subdirectory=common"

WORKDIR /work
COPY engine /opt/engine
ENV PYTHONPATH=/opt

RUN printf '#!/bin/sh\nexec python3 -m engine.forecast "$@"\n' > /usr/local/bin/forecast \
 && chmod +x /usr/local/bin/forecast

RUN useradd --create-home --uid 1000 runner
USER runner

CMD ["forecast", "--help"]
