# Phase 3: LLM Integration 詳細設計書 (Issue #2)

## 1. 概要

Gemini 3.8 Flash を使い、配信者としての発話（ゲーム実況・コメント応答）を生成する。

### 要件（Issue #2より）
- Gemini 3.8 Flash API との接続
- ゲーム実況生成（メインループ）
- コメント応答生成（サブループ/割り込み）

### 確定仕様
| 項目 | 内容 |
|------|------|
| モデル | `gemini-3.8-flash`（main / filter とも同じモデル） |
| SDK | `google-genai`（`genai.Client`）。旧 `google.generativeai` は使わない |
| 認証 | API キーのみ（`GEMINI_API_KEY`） |
| 生成パラメータ | `temperature` / `top_p` / `top_k` は 3.8 で廃止。`thinking_level` を枠ごとに設定（main=low, filter=low） |
| リトライ | 初回を含め最大3回、指数バックオフ（base=1s, max=10s）。対象は 408/429/5xx のみ（400 などは即失敗） |
| フォールバック | なし（別モデルへの切り替えはしない） |
| 失敗時の応答 | 空文字列 |
| レート制限 | リクエスト間隔を最低1秒あける |
| トークン監視 | ログ出力のみ |
| GameState | 任意（Phase 3 はゲーム状態なしで動く。Phase 6 まではテキスト要約で受け取る） |

### スコープ外（後続フェーズ）
- コメントフィルタ（filter 枠の利用）→ Phase 4
- 長期記憶・感情の自動更新 → Phase 5
- 構造化された GameState → Phase 6
- 実況ループ・割り込み制御と TTS への受け渡し → Phase 8（Phase 3 では `examples/integration_test_llm.py --speak` で手動確認）

## 2. コンポーネント構成

最上位を4層で分ける構成（`00_architecture_overview.md` §2.5）に従う。

```
src/ailoveshen/
├── domain/
│   ├── entities.py            # Conversation
│   ├── value_objects.py       # MessageRole, MessageType, ConversationMessage,
│   │                          # CharacterProfile, GenerationContext
│   ├── events.py              # CommentaryGeneratedEvent, ChatResponseGeneratedEvent
│   └── exceptions.py          # TextGenerationError
├── application/
│   ├── ports/
│   │   ├── input/
│   │   │   ├── generate_commentary.py   # IGenerateCommentary
│   │   │   └── generate_response.py     # IGenerateResponse
│   │   └── output/
│   │       ├── text_generator.py        # ITextGenerator
│   │       └── prompt_builder.py        # IPromptBuilder
│   ├── use_cases/
│   │   ├── generate_commentary.py       # GenerateCommentaryUseCase
│   │   └── generate_response.py         # GenerateResponseUseCase
│   └── dto/
│       └── llm_dto.py                   # Request / Response DTOs
├── infrastructure/
│   ├── config.py                        # GeminiSettings, CharacterSettings
│   └── adapters/
│       ├── gemini/
│       │   └── gemini_text_generator.py # GeminiTextGenerator (google-genai)
│       └── prompts/
│           └── prompt_template_builder.py  # PromptTemplateBuilder
├── presentation/
│   └── services/
│       └── llm_service.py               # LLMService
└── factories/
    └── llm.py                           # create_llm_service
```

**置き場所の判断**:
- プロンプトの文面はモデルに依存しないので `adapters/gemini/` ではなく `adapters/prompts/` に置く。別の LLM に替えても使い回せる
- Gemini 固有の概念（`thinking_level`、SDK の型、`"model"` ロールなど）は `GeminiTextGenerator` の中に閉じ込める。domain と application はモデル名や生成パラメータを知らない

## 3. Domain Layer

### 3.1 値オブジェクト (`domain/value_objects.py`)

| 型 | 役割 |
|----|------|
| `MessageRole` | `VIEWER` / `STREAMER`。LLM の `user`/`model` ではなく、配信ドメインの言葉で表す |
| `MessageType` | `CHAT`（視聴者コメント）/ `COMMENTARY`（実況）/ `RESPONSE`（返答） |
| `ConversationMessage` | 1件の発言。`from_viewer()` / `from_streamer()` で生成する。本文が空なら `ValueError`。時刻は UTC |
| `CharacterProfile` | 配信者キャラクター（名前・説明・話し方・一人称・語尾・性格）。名前が空なら `ValueError` |
| `GenerationContext` | 生成時に渡す状況（感情・ゲーム状態の要約・直近イベント・直近の会話）。整形はしない（純粋なデータ） |

### 3.2 エンティティ (`domain/entities.py`)

`Conversation(Entity)`: 配信の短期会話履歴。
- 最新 `max_history` 件だけを保持する（`deque(maxlen=max_history)`）。`max_history <= 0` は `ValueError`
- `add_viewer_message()` / `add_streamer_message()` / `recent_messages(limit)` / `recent_viewer_messages(limit)` / `clear()`
- 実況と応答のユースケースで同じインスタンスを共有する。これにより、実況は直前のコメントを踏まえ、応答は直前の実況を踏まえられる
- `@dataclass(eq=False)` とし、`Entity` の「ID による等価性」を保つ

### 3.3 ドメインイベント (`domain/events.py`)

- `CommentaryGeneratedEvent(text)`
- `ChatResponseGeneratedEvent(text, original_message, user_name)`

どちらも、生成テキストが空でなかったときだけ発行する。

## 4. Application Layer

### 4.1 Output Ports

```python
class ITextGenerator(ABC):
    async def generate(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """モデルが使えるテキストを返さなかったとき（ブロック等）は空文字列。
        API 呼び出しの失敗は TextGenerationError。"""

    async def close(self) -> None: ...


class IPromptBuilder(ABC):
    def build_system_prompt(self, character: CharacterProfile) -> str: ...
    def build_commentary_prompt(self, context: GenerationContext) -> str: ...
    def build_chat_response_prompt(
        self, user_name: str, message: str, context: GenerationContext
    ) -> str: ...
```

会話履歴をモデルの multi-turn 形式に変換する `generate_with_history` は持たない。履歴はプロンプトに文字列として埋め込む。
- 3.8 Flash のドキュメントは、モデルのターンを事前に埋めること（prefill）を推奨していない
- 仮に multi-turn 形式が必要になっても、ロール名の変換（`VIEWER`→`user` 等）は adapter の責務になる

### 4.2 Input Ports / DTOs (`application/dto/llm_dto.py`)

| DTO | フィールド |
|-----|-----------|
| `GenerateCommentaryRequest` | `emotion_state`, `recent_events: list[str]`, `game_state_summary: Optional[str]` |
| `GenerateCommentaryResponse` | `success`, `text`, `error`（`ok(text)` / `error_response(error)`） |
| `GenerateResponseRequest` | `user_name`, `message`, `user_id: Optional[str]`, `emotion_state` |
| `GenerateResponseResponse` | `success`, `original_message`, `user_name`, `text`, `error` |

`ok("")` は `success=False, error=None` になる。これで「空応答」と「例外」を区別する。

### 4.3 Use Cases

**`GenerateCommentaryUseCase`**
1. `GenerationContext` を組み立てる（直近イベントは最新 `max_recent_events`=3 件、会話履歴は最新 `history_limit`=10 件）
2. `IPromptBuilder` で system / user プロンプトを作る
3. `ITextGenerator.generate()`
4. テキストが空でなければ `Conversation` に `COMMENTARY` として記録し、`CommentaryGeneratedEvent` を発行する
5. 例外は捕捉して `error_response` を返す（テキストは空）

**`GenerateResponseUseCase`**
1. 生成に渡す履歴を先に取り出す。いま答えるコメントはプロンプトに別枠で載せるので、履歴には含めない
2. 視聴者コメントを `Conversation` に記録する。生成に失敗しても記録は残る
3. プロンプトを作って生成する
4. テキストが空でなければ `RESPONSE` として記録し、`ChatResponseGeneratedEvent` を発行する

## 5. Infrastructure Layer

### 5.1 GeminiTextGenerator (`infrastructure/adapters/gemini/gemini_text_generator.py`)

1インスタンスが1つのモデル枠（main または filter）を担当する。

```python
client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            attempts=3, initial_delay=1.0, max_delay=10.0, exp_base=2.0,
        ),
    ),
)
config = types.GenerateContentConfig(
    max_output_tokens=max_output_tokens,
    thinking_config=types.ThinkingConfig(thinking_level="low"),
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
response = await client.aio.models.generate_content(
    model=model,
    contents=prompt,
    config=config.model_copy(update={"system_instruction": system_instruction}),
)
```

| 項目 | 振る舞い |
|------|---------|
| 入力検証 | API キーが空なら `ValueError`。`thinking_level` は `low`/`medium`/`high` のみ受け付け、それ以外は `ValueError`。SDK は `minimal` を受け付けるが 3.8 Flash は拒否するので、起動時に弾く |
| リトライ | SDK の `HttpRetryOptions` に任せる。`attempts` は初回を含む。既定の対象は 408/429/5xx（ローカルの模擬サーバーで 503/429 は計3回、400 は1回で失敗することを確認済み） |
| レート制限 | `asyncio.Lock` で前回リクエストから `min_request_interval_seconds` 経つまで待つ |
| エラー | `errors.APIError` → `TextGenerationError("Gemini API error {code}: {message}")`、それ以外の例外 → `TextGenerationError` |
| 空応答 | `response.text` が空なら空文字列を返す。`prompt_feedback.block_reason` があればブロックとして、なければ `finish_reason` をログに出す |
| MAX_TOKENS | `max_output_tokens` には思考トークンも含まれる。上限に達すると出力が途切れるか空になるので、警告を出す（途中までのテキストはそのまま返す） |
| 使用量ログ | リクエストごとに所要時間（ms）と `prompt` / `thoughts` / `output` / `total` のトークン数を INFO で出す。`max_output_tokens` と `thinking_level` を調整する唯一の根拠になる |
| AFC | ツールを使わないので、自動関数呼び出しを明示的に無効化する |

### 5.2 PromptTemplateBuilder (`infrastructure/adapters/prompts/prompt_template_builder.py`)

`string.Template` による3つのテンプレート。
- **system**: キャラクター設定、性格、話し方、配信スタイル、注意事項。出力はそのまま音声合成されるため、記号・絵文字・注釈を禁止する
- **commentary**: ゲーム状況（なければ「不明」）、最近のイベント（箇条書き）、最近の会話（`名前: 本文`。配信者自身は「あなた」）、感情（例 `happy（強度: 0.8）`）、タスク（1-2文、直前の発言を繰り返さない）
- **chat response**: コメント（ユーザー名・本文）、最近の会話、感情、タスク（名前を呼ぶ、1-2文）

視聴者コメントは、そのまま **プロンプトに埋め込まれる**。プロンプトインジェクションへの対策は Phase 4 のコメントフィルタで行う。

## 6. Presentation Layer

`LLMService` (`presentation/services/llm_service.py`)
- `generate_commentary(recent_events=None, game_state_summary=None) -> str`
- `generate_response(user_name, message, user_id=None) -> str`
- `update_emotion(emotion)` / `get_current_emotion()`: 生成に使う感情を持つ。`TTSService` の `get_current_emotion` にも渡せる
- `close()`: `ITextGenerator` を閉じる
- 失敗時はどちらのメソッドも空文字列を返す

## 7. Composition Root (`factories/llm.py`)

```python
def create_llm_service(
    gemini: GeminiSettings,
    character: CharacterSettings,
    event_publisher: IEventPublisher,
    max_history: int = 20,
    history_limit: int = 10,
) -> LLMService:
```

- `GeminiTextGenerator` は main 枠（`main_model` / `main_thinking_level`）で作る
- `Conversation` を1つ作り、実況と応答の2つのユースケースで共有する
- `CharacterSettings` は `create_character_profile()` で `CharacterProfile` に変換する

## 8. 設定

```yaml
# config/default.yaml
gemini:
  api_key: "${GEMINI_API_KEY:-}"
  main_model: "gemini-3.8-flash"
  filter_model: "gemini-3.8-flash"
  main_thinking_level: "low"      # low / medium / high
  filter_thinking_level: "low"
  max_output_tokens: 8192         # 思考トークンを含む
  retry:
    max_attempts: 3               # 初回を含む
    base_delay_seconds: 1.0
    max_delay_seconds: 10.0
    exponential_base: 2.0
  rate_limit:
    min_interval_seconds: 1.0

character:
  name: "AILoveShen"
  description: |
    明るく元気なAI配信者。...
  speech_style: |
    フレンドリーで親しみやすい話し方。...
  first_person: "私"
  sentence_endings: ["だよ", "だね", "かな", "！"]
  personality_traits: ["好奇心旺盛", "ポジティブ", "ちょっとおっちょこちょい", "視聴者思い"]
```

`main_thinking_level` は実測（2026-09-24、同一プロンプトで各4回）をもとに **low** に決めた。

| thinking_level | 実況（中央値） | 返答（中央値） | 思考トークン |
|---|---|---|---|
| low | 1.90s | 1.64s | 0〜93 |
| medium | 2.87s | 3.10s | 129〜405 |

1〜2文の発話では品質に目立った差がなく、low のほうが約1.2秒速い。ライブ配信では返答の遅延がそのまま間の悪さになるので、速さを優先した。`max_output_tokens: 8192` は据え置く。実測の合計は最大でも約1,300トークンで、十分な余裕がある。途中で切れて空の応答になるほうが害が大きいので、上限は下げない。

## 9. テスト

| 対象 | ファイル |
|------|---------|
| Conversation | `tests/unit/domain/test_conversation.py` |
| 会話の値オブジェクト | `tests/unit/domain/test_conversation_value_objects.py` |
| イベント | `tests/unit/domain/test_events.py` |
| DTO | `tests/unit/application/test_llm_dto.py` |
| ユースケース | `tests/unit/application/test_generate_{commentary,response}_use_case.py` |
| Gemini adapter | `tests/unit/infrastructure/adapters/gemini/test_gemini_text_generator.py`（SDK の実際の型で応答を組み立てる。google-genai がなければスキップ） |
| プロンプト | `tests/unit/infrastructure/adapters/prompts/test_prompt_template_builder.py` |
| 設定 | `tests/unit/infrastructure/test_config.py` |
| LLMService | `tests/unit/presentation/test_llm_service.py` |
| Composition Root | `tests/unit/factories/test_llm_factory.py` |

手動確認:
- `python examples/demo_phase3.py`: 偽の生成器で全層を通す。API キー不要
- `python examples/integration_test_llm.py [--speak]`: 実 API で3回生成し、所要時間を表示する。`--speak` を付けると TTS で読み上げる
