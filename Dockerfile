FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Fonts with Cyrillic letters for the Telegram request cards: DejaVu always,
# Inter where the distribution has it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu fonts-dejavu-core \
    && (apt-get install -y --no-install-recommends fonts-inter || true) \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install .

# The API runs as an unprivileged user; the entrypoint (root) only fixes the
# ownership of the uploads volume and then drops to it.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /app/uploads && chown app:app /app/uploads

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
