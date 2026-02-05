# OmniParser v2 Inference Container
#
# Provides UI element detection using YOLO + EasyOCR + Florence-2
# Weights downloaded from HuggingFace on first startup

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

LABEL maintainer="noVNC Automation"
LABEL description="OmniParser v2 inference server (YOLO + OCR + Florence-2)"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    git \
    wget \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# Install PyTorch with CUDA support
RUN pip3 install --no-cache-dir \
    torch>=2.0.0 \
    torchvision>=0.15.0 \
    --index-url https://download.pytorch.org/whl/cu124

# Install ML dependencies (transformers pinned to match Florence-2 model config)
RUN pip3 install --no-cache-dir \
    transformers==4.46.1 \
    accelerate>=0.25.0 \
    safetensors>=0.4.0 \
    einops==0.8.0 \
    timm>=1.0.0 \
    supervision==0.18.0 \
    huggingface-hub>=0.20.0

# Install YOLO (pinned to match official OmniParser)
RUN pip3 install --no-cache-dir ultralytics==8.3.70

# Install EasyOCR (more stable than PaddleOCR)
RUN pip3 install --no-cache-dir easyocr>=1.7.0

# Install image processing
RUN pip3 install --no-cache-dir \
    Pillow>=10.0.0 \
    opencv-python-headless>=4.8.0 \
    numpy>=1.24.0

# Install FastAPI server
RUN pip3 install --no-cache-dir \
    fastapi>=0.109.0 \
    uvicorn>=0.27.0 \
    python-multipart>=0.0.6

# Create directories
RUN mkdir -p /app /app/weights /app/output

# Copy server
COPY server.py /app/server.py
RUN chmod +x /app/server.py

# Environment variables
ENV TRANSFORMERS_CACHE=/app/weights/transformers
ENV HF_HOME=/app/weights/huggingface
ENV OMNIPARSER_PORT=8000
ENV OMNIPARSER_HOST=0.0.0.0

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

WORKDIR /app

ENTRYPOINT ["python3", "-u", "/app/server.py"]
