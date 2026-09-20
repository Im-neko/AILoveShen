# Suggested Commands

## Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Initialize models (download BERT, pretrained weights)
python initialize.py
```

## Style-Bert-VITS2 (TTS)

### Web UI & Servers
```bash
# Main Gradio WebUI (training + inference)
cd Style-Bert-VITS2 && python app.py

# Editor server with browser
cd Style-Bert-VITS2 && python server_editor.py --inbrowser

# REST API server (port 5000)
cd Style-Bert-VITS2 && python server_fastapi.py
```

### Training Pipeline
```bash
cd Style-Bert-VITS2

# 1. Slice audio (2-12 sec segments)
python slice.py --model_name <name>

# 2. Transcribe (Whisper)
python transcribe.py --model_name <name>

# 3. Preprocess (normalize, BERT features)
python preprocess_all.py -m <model_name> --use_jp_extra

# 4. Train
python train_ms_jp_extra.py  # JP-Extra (recommended for Japanese)
python train_ms.py           # Standard multi-language

# 5. Generate style vectors
python style_gen.py -m <model_name>
```

### Testing & Linting
```bash
cd Style-Bert-VITS2

# Test
hatch run test:test           # PyTorch CPU
hatch run test:test-cuda      # PyTorch GPU
hatch run test-onnx:test      # ONNX CPU

# Style
hatch run style:check         # Check formatting
hatch run style:fmt           # Auto-format (black + isort)
```

### Model Conversion
```bash
cd Style-Bert-VITS2
python convert_onnx.py        # Convert to ONNX
python convert_bert_onnx.py   # Convert BERT to ONNX
```

## AILoveShen (Main - Not Yet Implemented)
```bash
# Future: Main streamer entry point
python streamer_main.py
```

## macOS (Darwin) Specific
- Use `brew` for system dependencies
- CUDA not available on macOS (use CPU or MPS)
- For Apple Silicon: consider using `onnxruntime-coreml`
