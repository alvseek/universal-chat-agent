FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source.
COPY . .

# Serve the FastAPI app (composition root exposes `app`).
CMD ["uvicorn", "application.main:app", "--host", "0.0.0.0", "--port", "8000"]
