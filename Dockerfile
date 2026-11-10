FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Fonts with Cyrillic letters for the Telegram request cards: DejaVu always,
# Inter where the distribution has it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl fonts-dejavu-core \
    && (apt-get install -y --no-install-recommends fonts-inter || true) \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install .

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
