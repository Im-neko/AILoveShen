#!/usr/bin/env bash
# ワールドと、ボットが覚えていること（家、記憶、大目標、メモ、技、動きの帳）をそろえて作り直す（docs/streaming.md「ワールドを作り直す」）。
#
#   scripts/reset_world.sh [--level NAME] [--seed SEED|random] [--existing] [--yes] [--dry-run]
#   mise run world:reset -- [同じ引数]
#
# 先に配信（python -m ailoveshen.stream）とブリッジ（npm start / npm run dev）を Ctrl-C で止めておく
# （動いたままだと、ブリッジが state.json を書き戻す。このスクリプトは止めない。動いていたら断る）。
#
# やること:
#   1. ワールドについての覚え（minecraft-bridge/data/state.json、minecraft-bridge/data/skills/、minecraft-bridge/data/worldmap/、data/mission.json、data/notes.json、
#      data/learned_candidates.json、data/goal.json）を、日付つきの名前にして取っておく（消さない）
#   2. .env の MC_LEVEL と MC_SEED を書き換える
#   3. Minecraft サーバーを作り直して（docker compose up --force-recreate）、"Done" が出るまで待つ
#   1b. 教訓のうち、失敗から書いて一度も効かなかったもの（tools/set_aside_lessons.py）を、data/lessons-unproven-日付.json に取り分ける
# 残るもの: 効いた教訓と教わった教訓、自分について話したこと、名前の読み（ワールドによらない）
#
#   --level NAME    新しいワールドの名前（既定: 今の名前の末尾の数字を 1 増やす。world2 → world3）
#   --seed SEED     シード（数字か文字。random で空 = ランダム。既定: 今のシードのまま = 同じ地形）
#   --existing      すでにあるワールドの名前を使う（前のワールドに戻る。そのときは取っておいた state.json などを戻す）
#   --yes           確認しない
#   --dry-run       何も変えず、やることだけ出す
set -Eeuo pipefail

ROOT="${RESET_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$ROOT/.env"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT/docker/docker-compose.minecraft.yml")
CONTAINER="ailoveshen-minecraft"
STATE="$ROOT/minecraft-bridge/data/state.json"
MISSION="$ROOT/data/mission.json"
NOTES="$ROOT/data/notes.json"
HANDS="$ROOT/data/hands.json" # 外した手の台帳（設計書 184）
LEARNED="$ROOT/data/learned_candidates.json"
GOAL="$ROOT/data/goal.json" # 今の小目標（設計書 63）
LESSONS="$ROOT/data/lessons.json" # 教訓帳。効かなかった失敗由来の教訓だけ取り分ける（設計書 201）
SKILLS="$ROOT/minecraft-bridge/data/skills"
WORLDMAP="$ROOT/minecraft-bridge/data/worldmap" # 視界の地図（設計書 59）
FORGOTTEN=("$STATE" "$SKILLS" "$WORLDMAP" "$MISSION" "$NOTES" "$HANDS" "$LEARNED" "$GOAL") # 新しいワールドでは意味が変わる覚え
WAIT_SECONDS=300
PYTHON="${RESET_PYTHON:-python3}"

level=""
seed="__keep__"
existing=0
yes=0
dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --level) level="${2:?--level に名前が要る}"; shift 2 ;;
    --seed) seed="${2:?--seed に値が要る（random でランダム）}"; shift 2 ;;
    --existing) existing=1; shift ;;
    --yes|-y) yes=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "知らない引数: $1（--help）" >&2; exit 2 ;;
  esac
done

die() { echo "エラー: $*" >&2; exit 1; }
say() { echo "==> $*"; }
run() { if [ "$dry" = 1 ]; then echo "    (dry-run) $*"; else "$@"; fi; }

[ -f "$ENV_FILE" ] || die "$ENV_FILE がない（cp .env.example .env）"
env_value() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
current_level="$(env_value MC_LEVEL)"; current_level="${current_level:-world}"
current_seed="$(env_value MC_SEED)"

# 新しい名前: 末尾の数字を 1 増やす（なければ 2）
if [ -z "$level" ]; then
  if [[ "$current_level" =~ ^(.*[^0-9])([0-9]+)$ ]]; then
    level="${BASH_REMATCH[1]}$((BASH_REMATCH[2] + 1))"
  else
    level="${current_level}2"
  fi
fi
[[ "$level" =~ ^[A-Za-z0-9_.-]+$ ]] || die "ワールドの名前は英数字と _ . - だけ: $level"
case "$seed" in
  __keep__) seed="$current_seed" ;;
  random) seed="" ;;
esac
[[ "$seed" =~ ^[A-Za-z0-9_.-]*$ ]] || die "シードは英数字と _ . - だけ（負の数は -123）: $seed"
if [ "$level" = "$current_level" ] && [ "$existing" = 0 ]; then
  die "今と同じ名前（${level}）では同じワールドのまま。別の名前にするか、--existing"
fi

# 配信は先頭の実行ファイルで絞る（zmx run などの包みに当たらない）
# 動いているものは止めない。止めてから来てもらう（ブリッジが動いたままだと state.json を書き戻す）
running=()
pgrep -f "^[^ ]*python[^ ]* -m ailoveshen.stream" >/dev/null 2>&1 && running+=("配信（python -m ailoveshen.stream）")
pgrep -f "node.*src/index.mjs" >/dev/null 2>&1 && running+=("ブリッジ（npm start / npm run dev）")
if [ "${#running[@]}" -gt 0 ]; then
  printf '動いている: %s\n' "${running[@]}" >&2
  die "先にそれぞれの端末で Ctrl-C で止めてから、もう一度（止めるのは自分でやってください）"
fi

# すでにそのワールドがあるか（あるのに新しい名前のつもりだと、前のワールドに入ってしまう）
if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  if docker exec "$CONTAINER" test -d "/data/$level" 2>/dev/null; then
    [ "$existing" = 1 ] || die "サーバーのボリュームに $level がもうある（前のワールドに入ってしまう）。別の名前か、戻るなら --existing"
  elif [ "$existing" = 1 ]; then
    die "--existing なのに、サーバーのボリュームに $level がない"
  fi
else
  echo "注意: $CONTAINER が動いていないので、$level がすでにあるかは確かめられない" >&2
fi

stamped() { # 取っておく先の名前（同じ日にもう取ってあれば時刻もつける。ファイルは .json の前、ディレクトリは末尾に）
  local f="$1" base="$1" ext="" out
  if [[ "$f" == *.json ]]; then base="${f%.json}"; ext=".json"; fi
  out="$base-$(date +%Y%m%d)$ext"
  [ -e "$out" ] && out="$base-$(date +%Y%m%d-%H%M%S)$ext"
  echo "$out"
}

say "ワールドを作り直す"
echo "    ワールド: $current_level → $level$([ "$existing" = 1 ] && echo '（すでにあるワールド）')"
echo "    シード:   ${current_seed:-(ランダム)} → ${seed:-(ランダム)}$([ "$seed" = "$current_seed" ] && echo '（同じ = 同じ地形）')"
for f in "${FORGOTTEN[@]}"; do
  if [ -e "$f" ]; then echo "    取っておく: ${f#"$ROOT"/} → $(basename "$(stamped "$f")")"; else echo "    （${f#"$ROOT"/} はない）"; fi
done
echo "    サーバーを作り直す: docker compose up -d --force-recreate（${CONTAINER}）"
"$PYTHON" "$ROOT/tools/set_aside_lessons.py" --lessons "$LESSONS" --out "$(stamped "$ROOT/data/lessons-unproven.json")" --dry-run | sed 's/^/    取り分ける教訓: /'
echo "    残るもの: 効いた教訓と教わった教訓、自分について話したこと、名前の読み"
if [ "$dry" = 1 ]; then echo "(dry-run: 何も変えていない)"; fi

if [ "$yes" = 0 ] && [ "$dry" = 0 ]; then
  read -r -p "進めますか？ [y/N] " answer
  case "$answer" in y|Y|yes) ;; *) echo "やめた"; exit 1 ;; esac
fi

# 途中で失敗したときに、戻し方を出す
moved=()
rollback() {
  echo >&2
  echo "失敗した。戻すなら:" >&2
  for pair in "${moved[@]:-}"; do [ -n "$pair" ] && echo "  mv '${pair#*|}' '${pair%%|*}'" >&2; done
  echo "  .env の MC_LEVEL=${current_level}、MC_SEED=${current_seed} に戻す" >&2
  echo "  取り分けた教訓（data/lessons-unproven-*.json）があれば、中身を data/lessons.json の lessons に足す" >&2
}
trap 'rollback' ERR

# 1. ワールドについての覚えを取っておく
for f in "${FORGOTTEN[@]}"; do
  [ -e "$f" ] || continue
  to="$(stamped "$f")"
  run mv "$f" "$to"
  moved+=("$f|$to")
done

# 1b. 教訓のうち、失敗から書いて一度も効かなかったものを取り分ける
if [ "$dry" = 1 ]; then
  echo "    (dry-run) 教訓を取り分ける"
else
  lessons_aside="$(stamped "$ROOT/data/lessons-unproven.json")"
  "$PYTHON" "$ROOT/tools/set_aside_lessons.py" --lessons "$LESSONS" --out "$lessons_aside"
fi

# 2. .env
set_env() { # KEY VALUE: 行があれば書き換え、なければ足す（.env の所有者と権限はそのまま）
  local tmp
  tmp="$(mktemp)"
  awk -v k="$1" -v v="$2" 'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; done = 1; next } { print } END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}
if [ "$dry" = 1 ]; then
  echo "    (dry-run) .env: MC_LEVEL=$level MC_SEED=$seed"
else
  set_env MC_LEVEL "$level"
  set_env MC_SEED "$seed"
fi

# 3. サーバーを作り直して待つ
say "サーバーを作り直す"
run "${COMPOSE[@]}" up -d --force-recreate
if [ "$dry" = 0 ]; then
  say "起動を待つ（新しいワールドは 1〜2 分。最長 ${WAIT_SECONDS} 秒）"
  waited=0
  until "${COMPOSE[@]}" logs --tail 100 minecraft 2>&1 | grep -q 'Done ('; do
    if [ "$waited" -ge "$WAIT_SECONDS" ]; then
      echo "エラー: サーバーが ${WAIT_SECONDS} 秒たっても起動しない（見る: ${COMPOSE[*]} logs -f）" >&2
      false # ERR の trap が戻し方を出す
    fi
    sleep 3
    waited=$((waited + 3))
  done
fi
trap - ERR

[ "$dry" = 1 ] && exit 0 # dry-run は、ここまでの「やること」だけ

say "できた。あとは、いつもの順に起動する"
cat <<EOF
    1. ブリッジ:           cd minecraft-bridge && npm start        （または npm run dev）
    2. 視点のクライアント:  127.0.0.1:25578 につなぐ
    3. 配信:               python -m ailoveshen.stream
    最初に畑を設計して水のバケツが揃ったら、ブリッジのログで "placed water at" を確かめる（設計書 47 §17: 実機で未確認）
    戻すなら: .env の MC_LEVEL=$current_level に戻し、取っておいた state-*.json、skills-*、worldmap-*、mission-*.json、notes-*.json、learned_candidates-*.json、goal-*.json を元の名前に戻し（取り分けた教訓 lessons-unproven-*.json は、中身を data/lessons.json の lessons に足す）、もう一度 up -d --force-recreate
EOF
