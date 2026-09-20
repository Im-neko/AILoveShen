# Code Style & Conventions

## Python Style
- **Formatter**: Black
- **Import sorting**: isort with `--profile black`
- **Lines after imports**: 2

## Type Hints
- Use type hints consistently
- Style-Bert-VITS2 uses Pydantic v2 for data models

## Docstrings
- Follow existing patterns in Style-Bert-VITS2
- Japanese comments are acceptable (project is Japanese-focused)

## Project Structure
```
AILoveShen/
├── CLAUDE.md              # AI assistant instructions
├── README.md              # Project documentation
├── data/                  # Voice data, transcripts, training
│   ├── raw/              # Raw audio files
│   ├── processed/        # Processed audio
│   ├── transcripts/      # Transcription files
│   ├── training/         # Training data
│   └── weights/          # Model weights
├── Style-Bert-VITS2/     # TTS submodule (git submodule)
│   ├── style_bert_vits2/ # Core library
│   │   ├── models/       # Neural network modules
│   │   ├── nlp/          # Text processing (JP/EN/ZH)
│   │   └── tts_model.py  # Main TTSModel class
│   ├── server_fastapi.py # REST API
│   ├── server_editor.py  # Editor UI
│   └── app.py            # Gradio WebUI
└── .serena/              # Serena MCP config
```

## Naming Conventions
- **Files**: snake_case.py
- **Classes**: PascalCase
- **Functions/Methods**: snake_case
- **Constants**: UPPER_SNAKE_CASE

## Key Classes
- `TTSModel`: Load and run inference
- `TTSModelHolder`: Manage multiple TTS models
- `Languages`: Enum for JP/EN/ZH

## Dependencies Management
- Use `requirements.txt` for main dependencies
- `pyproject.toml` for hatch environments
- Separate `requirements-infer.txt` for inference-only
