"""アバター（VRM）への合図のテスト（docs/design/24_avatar.md）。"""

import pytest

pytest.importorskip("fastapi", reason="ailoveshen[stream] not installed")

from fastapi.testclient import TestClient  # noqa: E402

from ailoveshen.domain.events import (  # noqa: E402
    CommentaryGeneratedEvent,
    GameActionExecutedEvent,
    GoalEndedEvent,
    MidGoalCompletedEvent,
    SpeechCompletedEvent,
    SpeechStartedEvent,
)
from ailoveshen.domain.value_objects import EmotionState, EmotionType  # noqa: E402
from ailoveshen.presentation.web.avatar import (  # noqa: E402
    AvatarStage,
    emotion_from_text,
    speech_ms,
)
from ailoveshen.presentation.web.goal_board import GoalBoard  # noqa: E402


def test_generated_text_moves_the_mouth_until_tts_takes_over():
    stage = AvatarStage(lip_sync="auto")
    [cue] = stage.cues_for(CommentaryGeneratedEvent(text="やった、家ができたよ！"))
    assert cue == {
        "type": "speak",
        "text": "やった、家ができたよ！",
        "duration_ms": speech_ms("やった、家ができたよ！"),
        "emotion": "happy",
    }
    # TTS の再生が始まったら、そちらに合わせる（本文の生成では二重に動かさない）
    [spoken] = stage.cues_for(
        SpeechStartedEvent(text="うわっ", emotion=EmotionState(), duration_ms=900)
    )
    assert (spoken["duration_ms"], spoken["emotion"]) == (900, "surprised")
    assert stage.cues_for(CommentaryGeneratedEvent(text="次の実況")) == []
    assert stage.cues_for(SpeechCompletedEvent(text="うわっ")) == [{"type": "quiet"}]


def test_text_only_ignores_tts_and_the_emotion_of_tts_wins_over_text_cues():
    assert AvatarStage(lip_sync="text").cues_for(SpeechStartedEvent(text="x")) == []
    [cue] = AvatarStage(lip_sync="tts").cues_for(
        SpeechStartedEvent(
            text="やった", emotion=EmotionState(EmotionType.SAD, 0.8), duration_ms=500
        )
    )
    assert cue["emotion"] == "sad"


def test_game_events_become_expressions_and_gestures():
    stage = AvatarStage()
    [cheer] = stage.cues_for(MidGoalCompletedEvent(title="家"))
    assert (cheer["emotion"], cheer["gesture"]) == ("happy", "cheer")
    [hurt] = stage.cues_for(
        GameActionExecutedEvent(
            action_id="dig", ok=False, result="failed: interrupted: took damage"
        )
    )
    assert (hurt["emotion"], hurt["gesture"]) == ("surprised", "flinch")
    [stuck] = stage.cues_for(
        GoalEndedEvent(
            goal="have(log, 3)", ended_because="goal have(log, 3) is stuck (actions keep failing)"
        )
    )
    assert stuck["emotion"] == "sad"
    assert stage.cues_for(GameActionExecutedEvent(action_id="dig", ok=True, result="dug")) == []


def test_text_cues_and_speech_length():
    assert emotion_from_text("うわっ、ゾンビだ！") == EmotionType.SURPRISED
    assert emotion_from_text("羊が見つからない…ざんねん") == EmotionType.SAD
    assert emotion_from_text("木を切りにいくね") is None
    assert speech_ms("") == 800
    assert speech_ms("あ" * 1000) == 20_000


def test_the_page_the_model_and_the_libraries_are_served(tmp_path):
    model = tmp_path / "a.vrm"
    model.write_bytes(b"glTF-model")
    client = TestClient(GoalBoard(lambda: None, avatar=AvatarStage(model_path=model)).app)
    assert "/api/avatar/stream" in client.get("/avatar").text
    assert client.get("/assets/avatar.vrm").content == b"glTF-model"
    assert client.get("/vendor/three-vrm/three-vrm.module.min.js").status_code == 200
    assert client.get("/vendor/three/build/three.module.js").status_code == 200
    missing = TestClient(GoalBoard(lambda: None, avatar=AvatarStage(model_path=None)).app)
    assert missing.get("/assets/avatar.vrm").status_code == 404
    with pytest.raises(ValueError, match="lip_sync"):
        AvatarStage(lip_sync="radio")
