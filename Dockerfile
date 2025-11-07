# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for gRPC and Google Cloud
# This includes C++ standard library and compilation tools
RUN apt-get update && apt-get install -y \
    libstdc++6 \
    g++ \
    gcc \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
# The system libraries installed above will allow gRPC to work properly
RUN pip install --no-cache-dir -r requirements.txt

# Copy mini_app package files first
COPY mini_app/package*.json ./mini_app/

# Install Node.js dependencies
RUN cd mini_app && npm install

# Copy the rest of the application
COPY . .

# Build the frontend
RUN cd mini_app && npm run build

# Expose port (Railway will inject PORT environment variable)
EXPOSE 8080

# Start the bot
CMD ["python", "start_server.py"]
