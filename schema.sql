-- 将棋の解析結果を置くテーブル。Supabase ダッシュボードの SQL Editor に貼って実行する。
-- 何度実行しても壊れないように if not exists / drop policy を付けてある。
--
-- 役割分担:
--   games  … 1局＝1行。一覧と集計に使う列を切り出し、ビューア用のJSONは data にまるごと持つ
--   moves  … 1手＝1行。「全対局の中で一番痛かった手」のような横断集計のため
--
-- 書き込みは Mac の sync.py が secret key でやる（RLSを素通りする）。
-- 公開ページは publishable key で読むだけ。RLS で anon には select しか許さない。

create table if not exists games (
  id            text primary key,          -- 開始日時 "20260918_1130"。games/*.kif のファイル名と同じ
  played_at     timestamptz not null,
  sente         text not null,
  gote          text not null,
  sente_rank    text,
  gote_rank     text,
  time_control  text,
  moves_count   int  not null,             -- 終局手（投了など）を除いた手数
  terminal      text,                      -- 投了 / 切れ負け / 千日手 …
  winner        text check (winner in ('b', 'w') or winner is null),  -- 先手勝ち=b / 後手勝ち=w / 引き分け・不明=null
  avg_loss_b    int,
  avg_loss_w    int,
  engine        text,
  eval_mode     text,                      -- NNUE / classical。ものさしが違う局を混ぜないための印
  eval_file     text,
  movetime      int,
  data          jsonb not null,            -- export.py のJSONそのもの。ビューアはこれを読む
  kif           text,                      -- games/*.kif の原本。ここにあれば Mac の games/ が消えても再解析できる
  synced_at     timestamptz not null default now()
);

create table if not exists moves (
  game_id  text not null references games(id) on delete cascade,
  n        int  not null,                  -- 手数（1始まり）
  side     text not null check (side in ('b', 'w')),
  kif      text not null,                  -- ７六歩(77)
  usi      text,                           -- 7g7f。終局手は null
  cp       int,                            -- この手を指した後の評価値（先手視点）
  loss     int,                            -- この手で失った評価値
  grade    text,                           -- '' / dubious / mistake / blunder
  primary key (game_id, n)
);

-- 2026-09-19 に kif 列を足した。それより前に作ったテーブルにも効くように
alter table games add column if not exists kif text;

create index if not exists moves_loss_idx on moves (loss desc);
create index if not exists games_played_at_idx on games (played_at desc);

-- 公開ページは誰でも読める。書けるのは secret key を持つ Mac だけ
alter table games enable row level security;
alter table moves enable row level security;
drop policy if exists "public read" on games;
drop policy if exists "public read" on moves;
create policy "public read" on games for select to anon using (true);
create policy "public read" on moves for select to anon using (true);
