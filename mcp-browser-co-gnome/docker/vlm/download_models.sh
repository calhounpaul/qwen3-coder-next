#!/bin/bash
# Download Qwen3-VL-4B model files from HuggingFace

set -e

MODEL_DIR="${VLM_MODEL_DIR:-/app/models}"
MODEL_REPO="${VLM_MODEL_REPO:-unsloth/Qwen3-VL-4B-Instruct-GGUF}"
MODEL_FILE="${VLM_MODEL_FILE:-Qwen3-VL-4B-Instruct-Q8_0.gguf}"
MMPROJ_FILE="${VLM_MMPROJ_FILE:-mmproj-F16.gguf}"

echo "Checking for VLM model files in $MODEL_DIR..."

# Download main model if missing
if [[ ! -f "$MODEL_DIR/$MODEL_FILE" ]]; then
    echo "Downloading $MODEL_FILE from $MODEL_REPO..."
    hf download "$MODEL_REPO" "$MODEL_FILE" --local-dir "$MODEL_DIR"
    echo "Downloaded $MODEL_FILE"
else
    echo "Model file already present: $MODEL_FILE"
fi

# Download mmproj if missing
if [[ ! -f "$MODEL_DIR/$MMPROJ_FILE" ]]; then
    echo "Downloading $MMPROJ_FILE from $MODEL_REPO..."
    hf download "$MODEL_REPO" "$MMPROJ_FILE" --local-dir "$MODEL_DIR"
    echo "Downloaded $MMPROJ_FILE"
else
    echo "Mmproj file already present: $MMPROJ_FILE"
fi

echo "All model files ready!"
