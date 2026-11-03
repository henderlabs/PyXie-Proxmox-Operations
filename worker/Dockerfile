FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/pyxie_core ./pyxie_core
# the wrapper kit: findings compares each host's wrapper version with the one shipped here (without it those findings never fired from the worker)
COPY api/host_maintenance_kit ./host_maintenance_kit
COPY worker/worker ./worker

RUN useradd --system --uid 10001 --no-create-home pyxie
USER pyxie

CMD ["python", "-m", "worker.main"]
