# 未決定事項一覧

このドキュメントは実装前に決定すべき技術仕様を記録します。
Phase 3から順に解決していきます。

---

## Phase 3: Gemini LLM Integration ✅ 確定

### 認証・接続
- [x] **Gemini API認証方式**: APIキー認証（OAuth2不要）
- [x] **モデル名**: `gemini-2.5-pro`（プレビュー版ではなく正式版）
- [x] **Rate Limit対策**: シンプルなsleep（1秒間隔）

### エラーハンドリング
- [x] **再試行ポリシー**: 3回リトライ、exponential backoff
- [x] **フォールバック**: なし（Flashモデルへの切り替えは行わない）
- [x] **デフォルト応答**: なし（失敗時は空文字列を返す）

### コスト管理
- [x] **トークン監視**: ログ出力のみ（アラートなし）

### GameState連携
- [x] **GameState**: Phase 3では不要（Optional[GameState] = None）
- [x] **コメンタリー生成**: GameStateなしでも動作可能にする


---

## Phase 4: Twitch Integration ✅ 確定

### 認証
- [x] **OAuthスコープ**: 初回起動時にブラウザ認証フロー実装（BAN等の権限含む）
- [x] **初期実装**: Client Credentials flowで開始、ブラウザ認証は後回し
- [x] **トークン管理**: 自動更新実装済み（TwitchAuth）

### フィルタリング
- [x] **動的閾値**: 5-30 MPM → 0.3-0.9 閾値（設計済み）
- [x] **NGワード**: Gemini Flashに委任（プロンプトで指示）
- [x] **フィルタ結果**: ログ出力 + ログファイル保存

### レート制限
- [x] **応答頻度**: 5秒に1回
- [x] **同一ユーザー制限**: なし

### 再接続
- [x] **サーバーエラー**: exponential backoffで無限リトライ
- [x] **クライアントエラー**: リトライしない（即座に失敗）


---

## Phase 5: MCP Server ✅ 確定

### メモリ管理
- [x] **短期メモリ容量**: 20件
- [x] **長期メモリ保存条件**: importance=HIGH以上は自動保存
- [x] **検索アルゴリズム**: SQLite LIKE検索（将来ベクトル検索追加予定）

### 感情状態
- [x] **減衰タイミング**: コメンタリー生成時に適用
- [x] **減衰率**: 0.1（設定済み）
- [x] **複合感情**: secondary emotionとして保持可能（設計済み）

### VTubeStudio連携
- [x] **APIバージョン**: VTube Studio API 1.0
- [x] **認証**: トークンファイル保存 (`data/.vts_token.json`)
- [x] **パラメータマッピング**: 設定ファイルで変更可能


---

## Phase 6: Game Integration ✅ 確定

**変更**: NitroGen → Jev (TypeSafe AI System Oneモデル) + Mineflayer に変更（設計: `docs/design/06_phase6_jev_integration.md`）

### アーキテクチャ
- [x] **抽象化レイヤー**: `IGameEnvironment` インターフェースで将来の他ゲーム対応
- [x] **Minecraft実装**: Mineflayer（Node.jsサイドカー） + Jev（判断）
- [x] **通信方式**: 外部で起動済みのMinecraftサーバーに接続

### 実装詳細
- [x] **フレームワーク**: Mineflayer (PrismarineJS) + Jev (TypeSafe AI)
- [x] **Node.js要件**: v18 or v20 LTS
- [x] **Minecraft対応**: Java Edition v1.21.6まで
- [x] **Python-Node連携**: HTTP API または WebSocket

### アクション実行
- [x] **タイムアウト**: 5秒
- [x] **並行実行**: 可（キュー管理）
- [x] **アクション種別**: MOVE, ATTACK, MINE, PLACE, USE_ITEM, CRAFT, INTERACT（定義済み）

### 将来の拡張
- 他ゲーム対応時は `IGameEnvironment` を実装するだけで追加可能
- 例: Terraria, Factorio, etc.


---

## Phase 7: OBS Integration ✅ 確定

### WebSocket接続
- [x] **OBSバージョン**: OBS 28以上（WebSocket 5.x）
- [x] **認証**: パスワード認証（設計済み）

### シーン制御
- [x] **シーン一覧**: OBS側で設定、configでマッピング
- [x] **トランジション**: OBSのデフォルト設定を使用

### 字幕表示
- [x] **表示位置/フォント**: OBS側で設定（AILoveShenでは管理しない）
- [x] **デフォルト表示時間**: 永続表示（duration未指定時は手動で消すまで）


---

## Phase 8: Orchestration

### メインループ
- [ ] **コメンタリー間隔**: 自動コメンタリーの生成間隔
- [ ] **トリガー条件**: ゲームイベントに基づくコメンタリー生成条件
- [ ] **中断条件**: サブループによる中断の条件

### サブループ
- [ ] **割り込み優先度**: コメント種別ごとの優先度
- [ ] **クールダウン**: 同一ユーザーからの連続応答制限
- [ ] **キュー管理**: 処理待ちコメントの優先順位付け

### セッション管理
- [ ] **統計収集**: 収集する統計項目（応答数、TTS時間、etc）
- [ ] **ログ出力**: セッションログの保存形式

---

## 全体的な技術選定

### 依存関係
- [ ] **asyncio vs threading**: 非同期処理の統一方針
- [ ] **httpx vs aiohttp**: HTTPクライアントの選定（現在httpx）

### 設定管理
- [ ] **環境別設定**: development/production/testの分離方法
- [ ] **シークレット管理**: APIキー等の安全な管理方法

### テスト戦略
- [ ] **結合テスト**: 外部API連携テストの方針（モック? ステージング?）
- [ ] **E2Eテスト**: 全体統合テストの範囲

---

## 解決済み事項

### Phase 1: Core Infrastructure ✅
- Entity, AggregateRoot, Value Objects
- AsyncEventBus, Settings, Logging

### Phase 2: TTS Pipeline ✅
- Style-Bert-VITS2 Client
- SounddevicePlayer
- Priority Queue

### Docker化 ✅
- TTS Server containerization
- Volume mounts for models
- Health checks

---

*Last Updated: 2026-01-10*
