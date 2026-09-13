FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock setuptools==82.0.1
COPY pyproject.toml ./
COPY src ./src
COPY services/api ./services/api
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .
COPY migrations ./migrations
COPY alembic.ini ./
COPY data/demo ./data/demo
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN groupadd -g 10001 kdaa && useradd -u 10001 -g kdaa -m kdaa \
    && mkdir -p /data/assets && chown -R kdaa:kdaa /data /app
USER kdaa
ENV ASSET_DIR=/data/assets DEMO_DIR=/app/data/demo
EXPOSE 8000
CMD ["sh", "scripts/entrypoint.sh"]

FROM runtime AS tests
USER root
COPY requirements-dev.lock ./
RUN python -m pip install --no-cache-dir -r requirements-dev.lock
COPY tests ./tests
COPY scripts/smoke_api.py ./scripts/smoke_api.py
USER kdaa
CMD ["python", "-m", "pytest", "-q"]
