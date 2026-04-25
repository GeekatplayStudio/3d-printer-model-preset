FROM python:3.12-slim

ARG INSTALL_BLENDER=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RESINLOGIC_BLENDER_EXECUTABLE=/usr/bin/blender

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && if [ "$INSTALL_BLENDER" = "1" ]; then apt-get install -y --no-install-recommends blender; fi \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY data ./data

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
