# P6 (Dev-board candidate): P1's hot path (one static simulate/simulate-batch binary, FROM scratch,
# no /etc, no shared library, no linker cache) + P3's ENV NVIDIA_DRIVER_CAPABILITIES=utility + the
# cheapest Python fallback we can carry: P5's trimmed + packed relocated fallback (/opt/pyfb + the
# engine /opt/jpsim, checked byte-identical to the untrimmed one in the build) as ONE xz-compressed
# tarball, /opt/jpfb/fallback.tar.xz, next to a static busybox. Regular files in the image: the
# binary (2 names), the launcher (2 names: simulate-py / simulate-batch-py), busybox, the tarball.
# The launcher unpacks the fallback only when the binary's scope check fails, into a private
# directory under the output location (writable, not noexec; /tmp is noexec), runs it through its
# own ELF loader and removes it before exiting.
# NVIDIA_VISIBLE_DEVICES / NVIDIA_REQUIRE_* are NOT set: the platform's GPU request is unchanged.
#   docker buildx build --platform linux/amd64 -t jpsim:p6 -f t3-work/Dockerfile.p6 t3-work
FROM --platform=linux/amd64 python:3.11-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ patchelf busybox-static strace xz-utils \
 && rm -rf /var/lib/apt/lists/*
COPY engine/jpkernel /src/jpkernel
RUN g++ -O2 -std=c++17 -fno-fast-math -ffp-contract=off -DJPK_NO_EXPORTS \
        -static -pthread -Wl,--gc-sections -ffunction-sections -fdata-sections -s \
        -o /src/jpsim-native /src/jpkernel/jpsim_native.cpp \
 && (ldd /src/jpsim-native 2>&1 | grep -q "not a dynamic executable")
RUN pip install --no-cache-dir "numpy==1.26.4" "pyarrow==15.0.2" "scipy==1.17.1"
COPY engine /stage/opt/jpsim
COPY probe/simulate-py probe/simulate-batch-py /stage/usr/local/bin/
RUN set -eu; S=/stage; P=$S/opt/pyfb; mkdir -p $P/bin $P/lib $S/usr/local/bin $S/etc $S/tmp; \
    find $S/opt/jpsim -type d -exec chmod 0755 {} +; find $S/opt/jpsim -type f -exec chmod 0644 {} +; \
    PYTHONPATH=$S/opt/jpsim python -m compileall -q -j 0 -o 0 -o 1 $S/opt/jpsim \
        /usr/local/lib/python3.11/site-packages/numpy /usr/local/lib/python3.11/site-packages/pyarrow; \
    rm -rf /usr/local/lib/python3.11/site-packages/pip* /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* /usr/local/lib/python3.11/ensurepip \
           /usr/local/lib/python3.11/idlelib /usr/local/lib/python3.11/tkinter /usr/local/lib/python3.11/turtledemo; \
    cp -a /usr/local/lib/python3.11 $P/lib/; \
    cp -L /usr/local/lib/libpython3.11.so.1.0 $P/lib/; \
    cp /usr/local/bin/python3.11 $P/bin/python3.11; \
    cp /bin/busybox $P/bin/sh; \
    for i in 1 2; do \
      find $P -type f \( -name '*.so' -o -name '*.so.*' -o -path "$P/bin/python3.11" \) -print0 \
        | xargs -0 -n 50 ldd 2>/dev/null | awk '$2 == "=>" && $3 ~ /^\// {print $3}' \
        | grep -v "^$P/" | sort -u | while read -r l; do cp -Ln "$l" $P/lib/; done; \
    done; \
    cp -L /lib64/ld-linux-x86-64.so.2 $P/lib/ld-linux-x86-64.so.2; \
    patchelf --set-interpreter /opt/pyfb/lib/ld-linux-x86-64.so.2 --set-rpath /opt/pyfb/lib $P/bin/python3.11; \
    cp /src/jpsim-native $S/usr/local/bin/simulate; \
    ln $S/usr/local/bin/simulate $S/usr/local/bin/simulate-batch; \
    ln $S/usr/local/bin/simulate $S/usr/local/bin/jpsim-native; \
    chmod 0755 $S/usr/local/bin/*; chmod 1777 $S/tmp; \
    printf 'root:x:0:0:root:/root:/sbin/nologin\nnobody:x:65534:65534:nobody:/nonexistent:/sbin/nologin\n' > $S/etc/passwd; \
    printf 'root:x:0:\nnogroup:x:65534:\n' > $S/etc/group; : > $S/etc/ld.so.conf; \
    ls $P/lib | grep -v '^python3.11$' | tr '\n' ' '; echo; \
    find $S -type f | wc -l
# P5 trim + pack (probe/trim_pyfb.py --pack): byte-identical outputs before/after, asserted in the build.
COPY probe/trim_pyfb.py tools/synthetic_check.py /src/trim/
RUN ln -s /stage/opt/pyfb /opt/pyfb && ln -s /stage/opt/jpsim /opt/jpsim \
 && python /src/trim/trim_pyfb.py /stage /src/trim/synthetic_check.py --pack \
 && rm /opt/pyfb /opt/jpsim && du -sh /stage/opt/pyfb /stage/opt/jpsim && find /stage -type f | wc -l
# P6 layout: everything the fallback needs goes into one tarball.
COPY probe/simulate-py-p6 /src/simulate-py-p6
RUN set -eu; S=/stage; F=/final; \
    rm -f $S/opt/pyfb/bin/sh; \
    mkdir -p $F/usr/local/bin $F/opt/jpfb $F/tmp; chmod 1777 $F/tmp; \
    cp /src/jpsim-native $F/usr/local/bin/simulate; ln $F/usr/local/bin/simulate $F/usr/local/bin/simulate-batch; \
    cp /src/simulate-py-p6 $F/usr/local/bin/simulate-py; ln $F/usr/local/bin/simulate-py $F/usr/local/bin/simulate-batch-py; \
    cp /bin/busybox $F/opt/jpfb/busybox; \
    chmod 0755 $F/usr/local/bin/simulate $F/usr/local/bin/simulate-py $F/opt/jpfb/busybox; \
    (cd $S/opt && tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -cf - pyfb jpsim) \
      | xz -9e --lzma2=preset=9e,dict=32MiB -T1 -c > $F/opt/jpfb/fallback.tar.xz; \
    chmod 0644 $F/opt/jpfb/fallback.tar.xz; \
    echo "fallback tree: $(find $S/opt/pyfb $S/opt/jpsim -type f | wc -l) files, $(du -sb $S/opt/pyfb $S/opt/jpsim | awk '{s+=$1} END {print s}') bytes"; \
    echo "P6 image: $(find $F -type f | wc -l) regular files ($(find $F -type f -links 1 | wc -l) single-link), $(du -sb $F | cut -f1) bytes"; \
    ls -la $F/usr/local/bin $F/opt/jpfb
# In-build self-test of the P6 launcher, with the fallback reached only through /opt/jpfb: run the
# extra-keys synthetic scenario (falls back) and a gbm-oracle one through the static binary, unpack
# under a fresh output directory, compare with the trimmed /stage fallback's own outputs, and require
# that nothing but the engine's files is left behind.
RUN set -eu; rm -rf /opt/jpfb && ln -s /final/opt/jpfb /opt/jpfb; mkdir -p /t; cd /t; \
    python -c "import importlib.util, json, pathlib; spec = importlib.util.spec_from_file_location('syn', '/src/trim/synthetic_check.py'); syn = importlib.util.module_from_spec(spec); spec.loader.exec_module(syn); fbs = [(n, sc) for n, sc, fb, can in syn.make_scenarios() if fb and can]; [pathlib.Path('/t/' + n + '.json').write_text(json.dumps(sc)) for n, sc in fbs]; print('fallback scenarios:', [n for n, _ in fbs])"; \
    for c in /t/*.json; do n=$(basename $c .json); mkdir -p /t/out-$n /t/ref-$n; \
      s=$(date +%s.%N); \
      JPSIM_PY_VERB=/final/usr/local/bin/simulate-py /final/usr/local/bin/simulate --config $c --out /t/out-$n/trace.parquet 2> /t/err-$n.txt || { cat /t/err-$n.txt; exit 1; }; \
      e=$(date +%s.%N); grep -q "running the Python engine" /t/err-$n.txt; \
      env -i LD_LIBRARY_PATH=/stage/opt/pyfb/lib PYTHONHOME=/stage/opt/pyfb PYTHONPATH=/stage/opt/jpsim HOME=/tmp JPSIM_KERNEL=py \
        /stage/opt/pyfb/lib/ld-linux-x86-64.so.2 --library-path /stage/opt/pyfb/lib /stage/opt/pyfb/bin/python3.11 -O -m jpsim.cli simulate --config $c --out /t/ref-$n/trace.parquet > /dev/null; \
      for f in trace.parquet message_trace.parquet; do cmp /t/out-$n/$f /t/ref-$n/$f; done; \
      left=$(ls -A /t/out-$n | tr '\n' ' '); echo "$n: launcher+unpack+run $(python -c "print(round($e - $s, 2))") s; output dir: $left"; \
      test "$(ls -A /t/out-$n | grep -c '^\.jpsim-fb')" = 0; \
    done; \
    mkdir -p /t/b /t/bout; cp /t/*.json /t/b/; \
    JPSIM_PY_VERB=/final/usr/local/bin/simulate-py /final/usr/local/bin/simulate-batch --batch-dir /t/b --out-dir /t/bout 2> /t/err-b.txt || { cat /t/err-b.txt; exit 1; }; \
    find /t/bout -name '.jpsim-fb*' | grep . && exit 1 || true; \
    for d in /t/bout/*/; do n=$(basename $d); for f in trace.parquet message_trace.parquet; do cmp $d/$f /t/ref-$n/$f; done; done; \
    echo "batch of $(ls /t/b | wc -l) fallback subs: byte-identical, nothing left behind"; \
    rm -rf /t /opt/jpfb

FROM scratch
COPY --from=build /final /
LABEL qfbench2.interface_version="2.0"
LABEL qfbench2.track="simulation"
LABEL org.opencontainers.image.source="https://github.com/dapeipeipeipei/agenthon"
LABEL org.opencontainers.image.description="Agenthon 2026 Track 3 simulator (jpsim): static C++ kernel binary + Python ABIDES fallback packed in one xz tarball, unpacked on demand (P6)"
ENV PATH=/usr/local/bin HOME=/tmp NVIDIA_DRIVER_CAPABILITIES=utility
WORKDIR /tmp
USER 65534:65534
CMD ["simulate", "--help"]
