FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx prometheus-client

COPY app.py .

ENV SERVICE_NAME=unknown
ENV SERVICE_PORT=8000
ENV DOWNSTREAM_SERVICES=""

EXPOSE 8000

CMD ["python", "app.py"]
