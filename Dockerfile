# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app
ENV PYTHONPATH=/app

# Install system dependencies required for gRPC and Google Cloud
# This includes C++ standard library and compilation tools
RUN apt-get update && apt-get install -y \
    libstdc++6 \
    g++ \
    gcc \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
# The system libraries installed above will allow gRPC to work properly
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port (Railway will inject PORT environment variable)
EXPOSE 8080

# Start with gunicorn (production WSGI server)
CMD ["gunicorn", "--config", "gunicorn_config.py", "start_server:app"]
