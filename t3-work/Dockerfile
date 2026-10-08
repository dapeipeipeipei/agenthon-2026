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

FROM --platform=linux/amd64 python:3.11-slim-bookworm

LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="simulation"
LABEL org.opencontainers.image.source="https://github.com/dapeipeipeipei/agenthon"
LABEL org.opencontainers.image.description="Agenthon 2026 Track 3 simulator (jpsim): pinned ABIDES baseline, accelerated, output-identical"

# PYTHONPATH carries site-packages explicitly because the verbs start the interpreter with -S
# (no site.py / .pth processing at every container start).
ENV PYTHONDONTWRITEBYTECODE=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    PYTHONPATH=/opt/jpsim:/usr/local/lib/python3.11/site-packages \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

# cramjam: snappy for jpsim.parquet_lite (a 2 ms import). pyarrow stays installed for the
# reference-identical JPSIM_PARQUET=pyarrow regression mode; the default run path never loads it.
RUN pip install --no-cache-dir "numpy==1.26.4" "pyarrow==15.0.2" "cramjam==2.13.0"

COPY engine /opt/jpsim
COPY bin/simulate /usr/local/bin/simulate
COPY bin/simulate-batch /usr/local/bin/simulate-batch

# Pre-compile every module (ours, site-packages AND the stdlib: the python:3.11-slim image strips
# the stdlib's .pyc files) at the -O level the verbs run at, so no run ever compiles source: the
# root filesystem is read-only at evaluation time, so nothing could be cached then anyway. The
# last line proves it: `python -v` must not report any code object built from a .py file.
RUN chmod 0755 /usr/local/bin/simulate /usr/local/bin/simulate-batch \
 && find /opt/jpsim -type d -exec chmod 0755 {} + \
 && find /opt/jpsim -type f -exec chmod 0644 {} + \
 && python -m compileall -q -j 0 -o 0 -o 1 /opt/jpsim \
 && python -m compileall -q -j 0 -o 1 -x '/(test|tests|idlelib|tkinter|turtledemo|lib2to3|ensurepip|pydoc_data|site-packages/pip|site-packages/setuptools)/' /usr/local/lib/python3.11 \
 && python -O -S -c "import sys; sys.modules['pandas']=None; import jpsim.cli, abides_core.abides, jpsim.config, jpsim.trace_fast, jpsim.parquet_lite; jpsim.trace_fast.writer_module(); assert jpsim.parquet_lite._compressor()[0] == 1, 'cramjam missing'; print('import ok, no pandas, -S ok, snappy ok')" \
 && python -O -S -v -c "import sys; sys.modules['pandas']=None; import jpsim.cli, abides_core.abides, jpsim.config, jpsim.trace_fast, jpsim.parquet_lite, cramjam" 2> /tmp/verbose.txt \
 && echo "$(grep -c 'code object from' /tmp/verbose.txt) code objects loaded, $(grep -c "code object from '.*[.]py'" /tmp/verbose.txt) compiled from source" \
 && ! grep -q "code object from '.*[.]py'" /tmp/verbose.txt \
 && rm /tmp/verbose.txt

# Never try to write .pyc at run time (the rootfs is read-only; the attempt is a wasted syscall).
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /tmp
USER 65534:65534

# No ENTRYPOINT: the harness passes the verb (`simulate ...` / `simulate-batch ...`) as the
# command and it resolves from PATH. No VOLUME anywhere in this image or its base.
CMD ["simulate", "--help"]
