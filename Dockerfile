# Imagem da aplicacao. O docker-compose sobe este servico so com --profile app.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias de sistema minimas do psycopg (binario) e de healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Planilhas e arquivos temporarios sobrevivem ao redeploy via volume.
RUN mkdir -p /app/tmp

EXPOSE 8000

# main.py ainda nao existe no repositorio; o compose so sobe este servico
# com --profile app depois que as rotas estiverem no ar. O comando fica
# preparado para esse momento.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
