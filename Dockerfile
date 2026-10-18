# Image for the web app, the migrate step and the ETL (same code, different commands).
FROM python:3.12.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/neighboar

COPY requirements.lock .
RUN pip install -r requirements.lock

RUN useradd --create-home --uid 10001 neighboar && chown neighboar:neighboar /srv/neighboar
COPY --chown=neighboar:neighboar . .
USER neighboar

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
