FROM python:3.12-slim

WORKDIR /app

# Build deps — only needed for packages that compile native extensions.
# PyMuPDF 1.24+ ships pre-built wheels, but keep gcc for any extras.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY . .

# Runtime directories (overridden by volume mounts in docker-compose)
RUN mkdir -p storage logs tmp

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:create_app()"]
