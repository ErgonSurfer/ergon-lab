# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
# SPDX-License-Identifier: MIT

FROM ubuntu@sha256:2edbbc5dc405e9612ba3584ce95480277e3eb374407b5505fe26f17df77c7dbc

ARG DEBIAN_FRONTEND=noninteractive

# The minimal Ubuntu image has no CA bundle. Fetch one exact package through
# BuildKit's checksum-verified remote ADD, extract only its public roots, and
# then use normal TLS plus Ubuntu archive signature verification for apt.
ADD --checksum=sha256:6e8cdcc8c86103acd4fc14649eac62ff2037108389074a7b167567af33c32245 \
    https://snapshot.ubuntu.com/ubuntu/20260901T000000Z/pool/main/c/ca-certificates/ca-certificates_20260601~22.04.1_all.deb \
    /tmp/ca-certificates.deb

RUN mkdir -p /tmp/ca-roots /etc/ssl/certs \
    && dpkg-deb -x /tmp/ca-certificates.deb /tmp/ca-roots \
    && cat /tmp/ca-roots/usr/share/ca-certificates/mozilla/*.crt \
        > /etc/ssl/certs/ca-certificates.crt \
    && sed -i \
        's|http://ports.ubuntu.com/ubuntu-ports|https://snapshot.ubuntu.com/ubuntu/20260901T000000Z|g' \
        /etc/apt/sources.list \
    && apt-get update -o Acquire::Check-Valid-Until=false \
    && apt-get install -y --no-install-recommends \
        build-essential=12.9ubuntu3 \
        ca-certificates=20260601~22.04.1 \
        cmake=3.22.1-1ubuntu1.22.04.2 \
        git=1:2.34.1-1ubuntu1.17 \
        libboost-chrono-dev=1.74.0.3ubuntu7 \
        libboost-filesystem-dev=1.74.0.3ubuntu7 \
        libboost-test-dev=1.74.0.3ubuntu7 \
        libboost-thread-dev=1.74.0.3ubuntu7 \
        libdb++-dev=1:5.3.21~exp1ubuntu4 \
        libdb-dev=1:5.3.21~exp1ubuntu4 \
        libevent-dev=2.1.12-stable-1build3 \
        libssl-dev=3.0.2-0ubuntu1.29 \
        ninja-build=1.10.1-1 \
        openssh-client=1:8.9p1-3ubuntu0.16 \
        python3=3.10.6-1~22.04.1 \
    && rm -rf /var/lib/apt/lists/* /tmp/ca-certificates.deb /tmp/ca-roots

ENV LANG=C \
    LC_ALL=C \
    NO_COLOR=1 \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    TERM=dumb \
    TZ=UTC

WORKDIR /reproduction
ENTRYPOINT ["python3", "/repository/tools/engineering/check_legacy_reproduction.py"]
