FROM node@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3

ARG PYTHON_VERSION=3.11.2-1+b1
ARG PIP_VERSION=23.0.1+dfsg-1
ARG PYTEST_VERSION=7.2.1-2

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        "python3=${PYTHON_VERSION}" \
        "python3-pip=${PIP_VERSION}" \
        "python3-pytest=${PYTEST_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 astra \
    && useradd --uid 65532 --gid 65532 --home-dir /home/astra \
        --create-home --shell /usr/sbin/nologin astra \
    && install --directory --owner=65532 --group=65532 --mode=0755 /workspace \
    && python3 --version \
    && python3 -m pip --version \
    && python3 -m pytest --version \
    && node --version \
    && npm --version

ENV HOME=/home/astra \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

USER 65532:65532
WORKDIR /workspace

CMD ["python3", "--version"]
