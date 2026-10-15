# syntax=docker/dockerfile:1

# ---- Build stage: install dependencies into a virtualenv ----
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
# Test-only packages (pytest, respx) are left out of the runtime image.
RUN python -m venv /opt/venv \
    && grep -viE '^(pytest|respx)([=<> ]|$)' requirements.txt > runtime-requirements.txt \
    && /opt/venv/bin/pip install -r runtime-requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin gleaner
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app/ ./app/
COPY static/ ./static/
COPY starter_feeds.yaml ./
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
