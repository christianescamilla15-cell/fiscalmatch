# FiscalMatch · API (FastAPI + SQLAlchemy 2)
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias van en su propia capa: cambian mucho menos que el codigo.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

# Usuario sin privilegios: el proceso NUNCA corre como root.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin fiscal \
    && chown -R fiscal:fiscal /app
USER fiscal

EXPOSE 8000

# Prueba de vida sin dependencias extra: el puerto acepta conexiones.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 3).close()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
