FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask

COPY alert-receiver.py .

EXPOSE 5000

CMD ["python", "alert-receiver.py"]
