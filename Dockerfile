FROM python:3.12-slim AS base

RUN groupadd -r etch && useradd -r -g etch -d /app etch
WORKDIR /app

COPY pyproject.toml README.md LICENSE.md ./
COPY etch/ etch/
RUN pip install --no-cache-dir ".[postgres]"

COPY site/ site/

# Pre-create the assent document store with correct ownership. Docker will
# adopt this as the initial state of the named volume on first boot.
RUN mkdir -p /data/assent-documents && chown -R etch:etch /data

USER etch
EXPOSE 8100

# --workers 1 is intentional. The anonymous assent endpoints use an in-memory
# sliding-window rate limiter that lives per-process; multiple workers would
# multiply the effective cap. When we move to a shared store (Redis), scale up.
CMD ["uvicorn", "etch.server:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
