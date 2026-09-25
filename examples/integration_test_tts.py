#!/usr/bin/env python3
"""本物の Style-Bert-VITS2 サーバーで TTS パイプラインを確かめる結合テスト。

前提:
1. Style-Bert-VITS2 のサーバーがポート 5001 で動いている
2. 依存をインストールしてある: pip install "ailoveshen[tts]"

使い方:
    python examples/integration_test_tts.py
"""

import asyncio
import sys
from pathlib import Path

# プロジェクトの src をパスに足す
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.factories.tts import create_tts_service, create_and_connect_tts_service


async def test_server_connection():
    """サーバーにつながるかを確かめる。"""
    print("=" * 60)
    print("テスト 1: サーバーへの接続")
    print("=" * 60)

    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:5001/models/info")
        if response.status_code == 200:
            models = response.json()
            print(f"サーバーに接続した")
            print(f"使えるモデル: {list(models.keys())}")
            for model_id, info in models.items():
                print(f"  モデル {model_id}: {info.get('id2spk', {})}")
            return True
        else:
            print(f"接続に失敗: {response.status_code}")
            return False


async def test_synthesis_only():
    """再生せずに合成だけを確かめる（ファイルに保存する）。"""
    print("\n" + "=" * 60)
    print("テスト 2: TTS の合成（再生なし）")
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

    print(f"合成中: {test_text}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "http://localhost:5001/voice",
            params=params,
        )

        if response.status_code == 200:
            audio_data = response.content
            print(f"音声データを {len(audio_data)} バイト受け取った")

            # 耳で確かめられるようにファイルに保存する
            output_path = Path(__file__).parent / "test_output.wav"
            output_path.write_bytes(audio_data)
            print(f"音声の保存先: {output_path}")
            return True
        else:
            print(f"合成に失敗: {response.status_code}")
            print(f"レスポンス: {response.text}")
            return False


async def test_tts_service():
    """再生まで含めて TTS サービス全体を確かめる。"""
    print("\n" + "=" * 60)
    print("テスト 3: TTS サービス全体（再生あり）")
    print("=" * 60)

    try:
        from ailoveshen.infrastructure.events import AsyncEventBus

        # イベントバスを作る
        event_bus = AsyncEventBus()

        # config/default.yaml に合わせた TTS の設定
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

        # TTS サービスを作って接続する
        service = await create_and_connect_tts_service(
            config=tts_config,
            event_publisher=event_bus,
        )
        print("TTS サービスを作って接続した")

        # 発話のリクエストをキューに入れる
        test_text = "AIラブシェンへようこそ！"
        print(f"発話をキューに入れる: {test_text}")

        request_id = await service.speak(
            text=test_text,
            source="integration_test",
        )
        print(f"リクエスト ID: {request_id}")

        # 終わるのを待つ
        print("発話が終わるのを待っている...")
        await asyncio.sleep(5)

        # サービスを止める
        await service.stop()
        print("TTS サービスを止めた")
        return True

    except Exception as e:
        print(f"TTS サービスのテストに失敗: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_emotion_based_speech():
    """感情に応じたスタイルの選び方を確かめる。"""
    print("\n" + "=" * 60)
    print("テスト 4: 感情に応じたスタイルの選択")
    print("=" * 60)

    from ailoveshen.domain.value_objects import EmotionState
    from ailoveshen.infrastructure.adapters.tts.emotion_style_service import EmotionStyleService

    emotion_service = EmotionStyleService()

    # 感情からスタイルへの対応を確かめる
    test_cases = [
        EmotionState(primary="neutral", intensity=0.5),
        EmotionState(primary="happy", intensity=0.8),
        EmotionState(primary="sad", intensity=0.6),
        EmotionState(primary="excited", intensity=0.9),
    ]

    for emotion in test_cases:
        style = emotion_service.get_style_for_emotion(emotion)
        print(f"  {emotion.primary} (強さ={emotion.intensity}) -> スタイル: {style}")

    return True


async def main():
    """結合テストをすべて実行する。"""
    print("=" * 60)
    print("AILoveShen TTS 結合テスト")
    print("=" * 60)
    print()

    results = {}

    # テスト 1: サーバーへの接続
    results["server_connection"] = await test_server_connection()

    # テスト 2: 合成だけ
    if results["server_connection"]:
        results["synthesis"] = await test_synthesis_only()
    else:
        results["synthesis"] = False
        print("\n合成のテストを飛ばす（サーバーにつながっていない）")

    # テスト 3: TTS サービス全体
    if results["synthesis"]:
        results["tts_service"] = await test_tts_service()
    else:
        results["tts_service"] = False
        print("\nTTS サービスのテストを飛ばす（合成に失敗した）")

    # テスト 4: 感情に応じたスタイル
    results["emotion_style"] = await test_emotion_based_speech()

    # まとめ
    print("\n" + "=" * 60)
    print("テストのまとめ")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("すべてのテストが通った")
    else:
        print("失敗したテストがある。上の出力を確認すること")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
