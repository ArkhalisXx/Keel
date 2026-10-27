FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 OMP_THREAD_LIMIT=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# Optional corporate/cloud CA bundle, supplied as a build secret rather than baked in.
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then \
      PIP_CERT=/run/secrets/build_ca pip install -r requirements.txt; \
    else pip install -r requirements.txt; fi
RUN useradd --create-home --uid 10001 keel
COPY --chown=keel:keel . .
RUN mkdir -p /app/uploads /app/staticfiles && chown -R keel:keel /app/uploads /app/staticfiles
USER keel
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "90", "--access-logfile", "-"]
