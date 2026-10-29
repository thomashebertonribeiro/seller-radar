FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["uvicorn", "seller_radar.web.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Alvo usado apenas para rodar a suíte; não entra na imagem de produção.
FROM base AS dev

USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
RUN chown -R app:app /app
USER app

CMD ["pytest", "-q"]
