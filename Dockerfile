FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential default-libmysqlclient-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 jobagents \
    && mkdir -p /app/var/workspaces /app/var/artifacts /app/var/log \
    && chown -R jobagents:jobagents /app

USER jobagents

EXPOSE 8000

CMD ["gunicorn", "--config", "deployment/gunicorn/gunicorn.conf.py", "wsgi:app"]
