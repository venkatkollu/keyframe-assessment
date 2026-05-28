FROM python:3.11-slim

# Install system dependencies (ffmpeg and build utilities)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy dependency specifications
COPY requirements.txt .

# Install PyTorch CPU-only version first to speed up builds and reduce image size
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install the rest of the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the Whisper tiny model to avoid runtime downloads
RUN python -c "import whisper; whisper.load_model('tiny')"

# Copy the rest of the application files
COPY . .

# Expose port for FastAPI
EXPOSE 7860

# Start FastAPI application using Uvicorn
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
