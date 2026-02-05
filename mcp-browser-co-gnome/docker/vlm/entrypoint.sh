#!/bin/bash
# Entrypoint: download models if needed, then start llama-server

set -e

MODEL_DIR="${VLM_MODEL_DIR:-/app/models}"
MODEL_FILE="${VLM_MODEL_FILE:-Qwen3-VL-4B-Instruct-Q8_0.gguf}"
MMPROJ_FILE="${VLM_MMPROJ_FILE:-mmproj-F16.gguf}"

# Download models if missing
/app/download_models.sh

# Start llama-server with VLM configuration
exec /app/llama-server \
    --model "$MODEL_DIR/$MODEL_FILE" \
    --mmproj "$MODEL_DIR/$MMPROJ_FILE" \
    --alias "Qwen3-VL-4B" \
    --n-gpu-layers 999 \
    --ctx-size 4096 \
    --port 8080 \
    --flash-attn on \
    "$@"
