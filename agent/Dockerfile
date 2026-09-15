# Scaffold only — see README.md. Not yet added to
# docker/development-easy/docker-compose.yml (ARCHITECTURE.md §1/§6: this
# service's placement in the Compose stack is decided; the observability
# backend's placement is not).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
