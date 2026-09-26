"""本番の配信のファクトリー（Composition Root、docs/streaming.md）。"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from loguru import logger

from ailoveshen.application.use_cases.readings import NameReadings
from ailoveshen.domain.entities import Conversation
from ailoveshen.domain.value_objects import SpeechPriority
from ailoveshen.factories.game import create_game_service
from ailoveshen.factories.llm import create_llm_service
from ailoveshen.infrastructure.adapters.storage import InMemoryGenerationLog, JsonReadingStore
from ailoveshen.infrastructure.config import Settings
from ailoveshen.infrastructure.events import AsyncEventBus
from ailoveshen.presentation.services import ChatResponder, Narrator
from ailoveshen.presentation.services.stream import Stream


class StreamSetupError(RuntimeError):
    """配信を始められない（TTS サーバーにつながらないなど）。理由と直し方を持つ。"""


async def create_stream(settings: Settings, tts_config: dict[str, Any], base_dir: Path) -> Stream:
    """
    配信の全部をつないだ Stream を作る。

    - 読み上げ（`stream.speak`）: TTS サーバーにつながらなければ始めない（黙った配信を防ぐ）
    - 目標ボード・アバター（`stream.board_port`、0 なら出さない）
    - Twitch のチャット（`twitch.enabled` かつ `twitch.channel`）: 匿名で読むだけ

    Args:
        settings: 設定
        tts_config: 設定の tts セクション（辞書。TTS のファクトリーが辞書で受け取る）
        base_dir: リポジトリの直下（アバターのモデルと記録の基準）

    Raises:
        StreamSetupError: 目標ボードのポートが使われている、または読み上げがオンで TTS サーバーに
            つながらないとき
    """
    if settings.stream.board_port:
        _check_port_free(settings.stream.board_port)
    bus = AsyncEventBus()
    conversation = Conversation()
    gemini_calls = InMemoryGenerationLog(settings.gemini.debug_log_size)
    # 視聴者の名前の読み（docs/design/30_name_readings.md）: 返答で覚え、読み上げで使う
    readings = NameReadings(JsonReadingStore(base_dir / settings.twitch.readings_path))
    game = create_game_service(
        gemini=settings.gemini,
        jev=settings.jev,
        minecraft=settings.minecraft,
        character=settings.character,
        event_publisher=bus,
        conversation=conversation,
        generation_log=gemini_calls,
        obs=settings.obs,
    )
    llm = create_llm_service(
        gemini=settings.gemini,
        character=settings.character,
        event_publisher=bus,
        conversation=conversation,
        mid_goals=game.mid_goals,
        generation_log=gemini_calls,
        readings=readings,
        notes=game.notes,
    )
    closers: list[Any] = []

    tts = None
    if settings.stream.speak:
        from ailoveshen.factories.tts import (
            create_and_connect_tts_service,
            describe_engine,
            engine_of,
        )

        try:
            tts = await create_and_connect_tts_service(
                config=tts_config,
                event_publisher=bus,
                get_current_emotion=llm.get_current_emotion,
                pronounce=readings.apply,
            )
        except Exception as e:  # noqa: BLE001 - どの失敗でも理由を言って始めない
            await game.close()
            await llm.close()
            how = (
                "Irodori-TTS-Server を起動し（scripts/irodori/start_mac.sh、docs/setup/irodori_tts.md）、"
                "curl http://localhost:8088/health で確かめる。"
                if engine_of(tts_config) == "irodori"
                else "cd docker && docker compose up -d で起動し、"
                "curl http://localhost:5001/models/info で確かめる。"
            )
            raise StreamSetupError(
                f"TTS サーバー（{describe_engine(tts_config)}）につながらない: {e}。"
                f"{how}読み上げなしで配信するなら --no-speak"
            ) from e
        closers.append(tts.stop)
        logger.info(f"[tts] 読み上げる（{describe_engine(tts_config)}）")

    def activity():
        return game.session.activity() if game.session else None

    # 読み上げの感情は、読む前に Jev がアバターの表情と一緒に選ぶ（設計書 34 §4）
    from ailoveshen.application.use_cases.avatar_director import (
        AvatarReaction,
        LineReactions,
        voice_emotion,
    )
    from ailoveshen.factories.avatar import create_avatar_director
    from ailoveshen.presentation.web.avatar import emotion_from_text

    reactions = LineReactions()
    voice_director = None
    if tts is not None and str(tts_config.get("emotion_judge", "jev")) == "jev":
        voice_director = create_avatar_director(
            settings.avatar, settings.jev, activity, base_dir=base_dir
        )
        if voice_director is not None:
            closers.append(voice_director.close)
            logger.info("[tts] 読み上げの感情は Jev が選ぶ")

    async def voiced(text: str):
        """読む前に選ぶ感情（選べなければ None: 中立のまま）。"""
        if voice_director is None:
            return None
        rule = emotion_from_text(text)
        reaction = await voice_director.react(
            "speaking", text, AvatarReaction(emotion=rule.value if rule else None)
        )
        reactions.remember(text, reaction)
        return voice_emotion(reaction)

    async def say(text: str) -> None:
        logger.info(f"[say] {text}")
        if tts is not None:
            await tts.speak(text, emotion=await voiced(text), source="commentary")

    async def say_reply(text: str) -> None:
        if tts is not None:
            # 視聴者への返事は実況より先に読む
            await tts.speak(
                text, priority=SpeechPriority.HIGH, emotion=await voiced(text), source="chat"
            )

    Narrator(llm, activity=activity, say=say).subscribe(bus)

    board = None
    if settings.stream.board_port:
        from ailoveshen.factories.avatar import create_avatar_stage
        from ailoveshen.presentation.web.goal_board import GoalBoard

        avatar = create_avatar_stage(
            settings.avatar,
            settings.jev,
            activity,
            base_dir=base_dir,
            reactions=reactions if voice_director is not None else None,
        )
        avatar.subscribe(bus)
        closers.append(avatar.close)
        board = GoalBoard(
            activity,
            gemini_calls=gemini_calls.recent,
            gemini_image=gemini_calls.image,
            avatar=avatar,
            readings=readings.all,
        )
        board.subscribe(bus)

    chat = responder = None
    twitch = settings.twitch
    if twitch.enabled and twitch.channel.strip():
        from ailoveshen.infrastructure.adapters.twitch import TwitchIrcChat

        # トークンがあれば、そのアカウントでログインしてチャットに書ける（!commands の一覧）
        chat = TwitchIrcChat(twitch.channel, login=twitch.bot_login, token=twitch.access_token)
        # 返事の前に Jev が仕分ける（設計書 34 §5）
        triage = None
        if twitch.triage == "jev" and settings.jev.api_key:
            from ailoveshen.application.use_cases.comment_triage import CommentTriage
            from ailoveshen.infrastructure.adapters.jev.jev_fast_judge import JevFastJudge
            from ailoveshen.infrastructure.adapters.storage.jsonl_watch_recorder import (
                JsonlWatchRecorder,
            )

            triage = CommentTriage(
                judge=JevFastJudge(
                    api_key=settings.jev.api_key,
                    model=settings.jev.model,
                    timeout_seconds=settings.jev.timeout_seconds,
                ),
                activity=activity,
                skip_min_confidence=twitch.skip_min_confidence,
                recorder=JsonlWatchRecorder(str(base_dir / twitch.triage_record_dir))
                if twitch.triage_record_dir
                else None,
            )
            closers.append(triage.close)
        responder = ChatResponder(
            llm,
            session=lambda: game.session,
            say=say_reply,
            min_interval_seconds=twitch.min_interval_seconds,
            backlog=twitch.backlog,
            readings=readings,
            chat=chat,
            # 配信者自身と書き込むアカウントのコメントには返事をしない（コマンドは実行する）。
            # ボット（StreamElements など）には何も反応しない
            quiet=(chat.channel, chat.login),
            ignore=twitch.ignore_users,
            refresh=game.refresh,
            triage=triage.triage if triage is not None else None,
        )
        how = f"{chat.login} で書き込みもする" if chat.login else "読むだけ"
        logger.info(f"[chat] Twitch #{chat.channel} のチャットを読む（{how}）")
    else:
        logger.info("[chat] Twitch のチャットは読まない（.env の TWITCH_CHANNEL が空か、オフ）")

    stream = Stream(
        game,
        llm,
        board=board,
        board_port=settings.stream.board_port,
        chat=chat,
        responder=responder,
        closers=tuple(closers),
    )
    stream.subscribe(bus)
    logger.info(f"[control] {settings.minecraft.control}")
    return stream


def _check_port_free(port: int, host: str = "127.0.0.1") -> None:
    """目標ボードのポートが空いているか（使われていると OBS のソースが空のまま配信が進む）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as e:
            raise StreamSetupError(
                f"目標ボードのポート {port} が使われている（{e.strerror}）。前の配信や "
                "examples/integration_test_minecraft.py が動いていないか確かめる。"
                "別のポートなら --board-port（OBS のブラウザソースの URL も合わせる）"
            ) from e
