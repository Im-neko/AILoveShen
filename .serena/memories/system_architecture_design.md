# AILoveShen System Architecture Design

## Status: Design Complete

Detailed design documents have been created in `docs/design/`:

| Document | Content |
|----------|---------|
| `00_architecture_overview.md` | System overview, component diagram, tech stack |
| `01_phase1_core_infrastructure.md` | Directory structure, config, models, events, exceptions |
| `02_phase2_tts_pipeline.md` | Style-Bert-VITS2 client, speech queue, audio output |
| `03_phase3_llm_integration.md` | Gemini client, prompts, context management |
| `04_phase4_twitch_integration.md` | IRC client, OAuth, comment filter (dynamic threshold) |
| `05_phase5_mcp_server.md` | Memory (short/long term), emotion, expression |
| `06_phase6_jev_integration.md` | Jev decision loops (reactive/tactical), Mineflayer bridge, game state manager |
| `07_phase7_obs_integration.md` | WebSocket client, scenes, subtitles |
| `08_phase8_orchestration.md` | Main loop, sub loop, orchestrator |

## Implementation Order

1. **Phase 1**: Core Infrastructure (config, events, models)
2. **Phase 2**: TTS Pipeline (Style-Bert-VITS2)
3. **Phase 3**: LLM Integration (Gemini 2.5)
4. **Phase 4**: Twitch Integration (IRC + Flash filter)
5. **Phase 5**: MCP Server (memory, emotion)
6. **Phase 6**: Jev + Minecraft Bridge Integration
7. **Phase 7**: OBS Integration
8. **Phase 8**: Orchestration (full integration)