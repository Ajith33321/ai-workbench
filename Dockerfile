FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY README.md INTERVIEW_NOTES.md ./

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
