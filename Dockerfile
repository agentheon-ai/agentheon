FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# NOTICE is listed in pyproject's license-files, so the build needs it present.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY agentheon ./agentheon
RUN pip install ".[search,mcp,postgres]"

COPY examples ./examples
COPY governance.example.yaml ./governance.yaml

RUN useradd --create-home --uid 10001 agentheon && mkdir -p /app/skills /app/data \
    && chown -R agentheon /app
USER agentheon

ENV DATABASE_URL=sqlite:////app/data/agentheon.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready')"
CMD ["agentheon", "serve", "--host", "0.0.0.0", "--port", "8000"]
