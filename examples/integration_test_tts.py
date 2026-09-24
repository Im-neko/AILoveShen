#!/usr/bin/env python3
"""Integration test for TTS pipeline with real Style-Bert-VITS2 server.

Prerequisites:
1. Style-Bert-VITS2 server running on port 5001
2. Dependencies installed: pip install "ailoveshen[tts]"

Usage:
    python examples/integration_test_tts.py
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.factories.tts import create_tts_service, create_and_connect_tts_service


async def test_server_connection():
    """Test basic server connection."""
    print("=" * 60)
    print("Test 1: Server Connection")
    print("=" * 60)

    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:5001/models/info")
        if response.status_code == 200:
            models = response.json()
            print(f"Connected to server successfully")
            print(f"Available models: {list(models.keys())}")
            for model_id, info in models.items():
                print(f"  Model {model_id}: {info.get('id2spk', {})}")
            return True
        else:
            print(f"Failed to connect: {response.status_code}")
            return False


async def test_synthesis_only():
    """Test synthesis without audio playback (saves to file)."""
    print("\n" + "=" * 60)
    print("Test 2: TTS Synthesis (No Playback)")
    print("=" * 60)

    import httpx

    test_text = "こんにちは、私はシェンです。"
    params = {
        "text": test_text,
        "model_id": 0,
        "speaker_id": 0,
        "language": "JP",
        "style": "Neutral",
        "sdp_ratio": 0.2,
        "noise": 0.6,
        "noisew": 0.8,
        "length": 1.0,
    }

    print(f"Synthesizing: {test_text}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "http://localhost:5001/voice",
            params=params,
        )

        if response.status_code == 200:
            audio_data = response.content
            print(f"Received {len(audio_data)} bytes of audio data")

            # Save to file for manual verification
            output_path = Path(__file__).parent / "test_output.wav"
            output_path.write_bytes(audio_data)
            print(f"Saved audio to: {output_path}")
            return True
        else:
            print(f"Synthesis failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False


async def test_tts_service():
    """Test full TTS service with audio playback."""
    print("\n" + "=" * 60)
    print("Test 3: Full TTS Service (With Playback)")
    print("=" * 60)

    try:
        from ailoveshen.infrastructure.events import AsyncEventBus

        # Create event bus
        event_bus = AsyncEventBus()

        # TTS configuration matching config/default.yaml
        tts_config = {
            "server": {
                "host": "localhost",
                "port": 5001,
                "timeout_seconds": 30,
            },
            "voice": {
                "model_name": "shen",
                "speaker_id": 0,
                "language": "JP",
                "default_style": "Neutral",
            },
            "synthesis": {
                "sdp_ratio": 0.2,
                "noise": 0.6,
                "noisew": 0.8,
                "length": 1.0,
            },
            "queue": {"max_size": 10},
            "audio": {"device": None, "blocksize": 1024},
        }

        # Create and connect TTS service
        service = await create_and_connect_tts_service(
            config=tts_config,
            event_publisher=event_bus,
        )
        print("Created and connected TTS service")

        # Queue a speech request
        test_text = "AIラブシェンへようこそ！"
        print(f"Queueing speech: {test_text}")

        request_id = await service.speak(
            text=test_text,
            source="integration_test",
        )
        print(f"Request ID: {request_id}")

        # Wait for completion
        print("Waiting for speech to complete...")
        await asyncio.sleep(5)

        # Stop the service
        await service.stop()
        print("Stopped TTS service")
        return True

    except Exception as e:
        print(f"TTS Service test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_emotion_based_speech():
    """Test speech with emotion-based style selection."""
    print("\n" + "=" * 60)
    print("Test 4: Emotion-Based Style Selection")
    print("=" * 60)

    from ailoveshen.domain.value_objects import EmotionState
    from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService

    emotion_service = EmotionStyleService()

    # Test emotion to style mapping
    test_cases = [
        EmotionState(primary="neutral", intensity=0.5),
        EmotionState(primary="happy", intensity=0.8),
        EmotionState(primary="sad", intensity=0.6),
        EmotionState(primary="excited", intensity=0.9),
    ]

    for emotion in test_cases:
        style = emotion_service.get_style_for_emotion(emotion)
        print(f"  {emotion.primary} (intensity={emotion.intensity}) -> Style: {style}")

    return True


async def main():
    """Run all integration tests."""
    print("=" * 60)
    print("AILoveShen TTS Integration Tests")
    print("=" * 60)
    print()

    results = {}

    # Test 1: Server connection
    results["server_connection"] = await test_server_connection()

    # Test 2: Synthesis only
    if results["server_connection"]:
        results["synthesis"] = await test_synthesis_only()
    else:
        results["synthesis"] = False
        print("\nSkipping synthesis test (server not connected)")

    # Test 3: Full TTS service
    if results["synthesis"]:
        results["tts_service"] = await test_tts_service()
    else:
        results["tts_service"] = False
        print("\nSkipping TTS service test (synthesis failed)")

    # Test 4: Emotion-based style
    results["emotion_style"] = await test_emotion_based_speech()

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All tests passed!")
    else:
        print("Some tests failed. Check the output above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
