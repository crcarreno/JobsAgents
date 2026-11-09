FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 sandbox

USER sandbox

WORKDIR /workspace

CMD ["/bin/sh"]
