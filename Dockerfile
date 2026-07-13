# SHIMS Omni + Enterprise — Container Image
# Build:  docker build -t shims:latest .
# Run:    docker-compose up -d

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (some Python packages need compilers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole project
COPY . .

# Ensure required directories exist
RUN mkdir -p data storage generated media

# Run database migrations at build time (optional; also run at startup)
RUN python -m alembic upgrade head || true

# Expose both service ports
EXPOSE 8010 8020

# Default entrypoint runs the Omni brain on 8010.
# Override via docker-compose for Enterprise on 8020.
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8010"]
