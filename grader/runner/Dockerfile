# Build context: syslab-infra/grader. Build only after approving local Docker use.
FROM debian:bookworm-slim AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends python3 gcc g++ libc6-dev libseccomp-dev \
    && rm -rf /var/lib/apt/lists/*
COPY runner/launcher.c /build/launcher.c
RUN gcc -O2 -Wall -Wextra -Werror /build/launcher.c -lseccomp -o /build/launcher

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 libseccomp2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Chroot intentionally excludes /proc, /sys, /dev, /etc and the trusted worker.
COPY --from=toolchain /usr /jail/usr
COPY --from=toolchain /lib /jail/lib
COPY --from=toolchain /lib64 /jail/lib64
RUN ln -s usr/bin /jail/bin && mkdir -p /work /opt/runner
COPY --from=toolchain /build/launcher /opt/runner/launcher
COPY execution_protocol.py runner/sandbox.py runner/worker.py /opt/runner/
WORKDIR /opt/runner
USER 0
CMD ["python3", "-B", "/opt/runner/worker.py"]
