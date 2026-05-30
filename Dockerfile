FROM python:3.11-slim

WORKDIR /app

# System deps: lxml needs libxml2/libxslt at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENV NEWSGRAPH_DB_PATH=/data/newsgraph.sqlite3 \
    NEWSGRAPH_CACHE_DIR=/data/cache
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "newsgraph.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
