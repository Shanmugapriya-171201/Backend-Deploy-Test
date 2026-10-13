# Not used by the Render deploy (native Python runtime). Kept as the exit plan:
# the same container runs on Cloud Run, Azure, AWS or any Docker host.
# Build from the repo root:  docker build -t datapulse-backend .
# Run:                       docker run -p 10000:10000 -e API_KEY=local datapulse-backend
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY knowledge-base/ knowledge-base/

EXPOSE 10000
CMD ["sh", "-c", "uvicorn main:app --app-dir backend --host 0.0.0.0 --port ${PORT} --workers 1"]
