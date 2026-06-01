FROM python:3.11-slim

WORKDIR /app

# System deps for lxml, sentence-transformers, crawl4ai
RUN apt-get update && apt-get install -y \
    libxml2-dev libxslt1-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8000

CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]