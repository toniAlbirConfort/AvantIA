FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic

FROM base AS api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
CMD ["sh", "-c", "exec xvfb-run -a python -m app.worker"]
