# AILoveShen Project Overview

## Purpose
AILoveShen is an **AI Streamer** project for Twitch that combines:
- **Jev + Mineflayer**: Minecraft game control — Jev (TypeSafe AI System One model) makes fast typed decisions, Mineflayer executes them
- **Gemini 2.5**: Main conversation/commentary generation
- **Gemini Flash**: Comment filtering with dynamic threshold
- **Style-Bert-VITS2**: BERT-based TTS with emotional style control
- **MCP (Model Context Protocol)**: Memory management, expression control

## Architecture
```
Main Loop: Minecraft Bridge (Mineflayer) (game) → Gemini 2.5 (commentary) → Style-Bert-VITS2 (TTS)
Sub Loop: Twitch Chat → Gemini Flash (filter) → Gemini 2.5 (response) → TTS (interrupt)
```

### Three-layer LLM → Jev → Minecraft Bridge
- **LLM → Jev**: Gemini picks a `Goal` (closed set of 13) and hands it to Jev
- **Jev → Minecraft Bridge**: Reactive Loop (~600ms, survival reflexes, no LLM) and Tactical Loop (~10s, next step within the Goal)
- **Minecraft Bridge → LLM/Jev**: Game State Manager feeds position, inventory, surroundings, events back to both

## Tech Stack
- **Python 3.10+** (requires 3.9+ for Style-Bert-VITS2)
- **PyTorch** with CUDA 11.8+ support
- **FastAPI** for TTS API server
- **Gradio** for Web UI (training + inference)
- **hatch** for build/test management
- **Twitch API** (IRC/EventSub)
- **OBS WebSocket** for stream control

## Current State
- Style-Bert-VITS2 is integrated as a submodule (fully functional)
- Main AILoveShen components are **not yet implemented** (see GitHub Issues #1-#7)

## Open Issues
1. Twitch Integration (chat retrieval)
2. Gemini 2.5 Conversation System
3. MCP Server Implementation
4. Minecraft Integration (Jev + Mineflayer)
5. Real-time TTS Pipeline
6. OBS Integration
7. Gemini Flash Comment Filtering
