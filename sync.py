"""解析済みJSON（out/*.json）を Supabase へ送る。公開ページ（docs/）はこれを読む。

    python3 sync.py                    # Supabase にまだ無い局だけ送る（19時のバッチはこれ）
    python3 sync.py out/20260918_1130.json   # この1局を送る（既にあれば上書き）
    python3 sync.py --all              # 全部送り直す（テーブルを作り直したとき）
    python3 sync.py --dry-run          # 何を送るかだけ見る

標準ライブラリだけで PostgREST を直接叩く（supabase-py は入れない。README の
「pip install するものは無い」を守るため）。

鍵は `~/claude/application/MCP/.env` の SUPABASE_URL / SUPABASE_SECRET_KEY を借りる
（環境変数が優先）。secret key は RLS を素通りする管理者鍵なので、このリポジトリには置かない。
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
GAMES = HERE / "games"
ENV_FILE = Path.home() / "claude/application/MCP/.env"
JST = timezone(timedelta(hours=9))

# 終局手の側が負けたのか勝ったのか。KIFの終局手は「その手番の側の行動」として書かれる
LOSER_ENDS = ("投了", "切れ負け", "反則負け", "詰み")
WINNER_ENDS = ("反則勝ち", "入玉勝ち")


# ── 設定 ─────────────────────────────────────────────────────────────────

def env(name):
    if os.environ.get(name):
        return os.environ[name]
    if ENV_FILE.exists():
        m = re.search(rf"^{name}=(.+)$", ENV_FILE.read_text(), re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    sys.exit(f"{name} が見つかりません（{ENV_FILE} か環境変数）。\n"
             "  Supabase ダッシュボード → Project Settings → API Keys の Secret key を\n"
             f"  {ENV_FILE} に SUPABASE_SECRET_KEY=sb_secret_... として追記してください")


class DbError(Exception):
    """Supabase に届かない／HTTPエラー。呼び出し側で局ごとに拾えるよう、SystemExit にしない"""


class Db:
    def __init__(self):
        self.url = env("SUPABASE_URL").rstrip("/")
        self.key = env("SUPABASE_SECRET_KEY")

    def request(self, path, method="GET", body=None, prefer=None):
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                   "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise DbError(f"Supabase {method} {path} → HTTP {e.code}\n{detail}") from None
        except urllib.error.URLError as e:
            raise DbError(f"Supabase に届きません: {e.reason}\n"
                          "  プロジェクトが pause していないかダッシュボードで確認してください"
                          "（無料枠は7日無活動で止まる）") from None

    def remote_ids(self):
        # 19時のバッチが毎日ここを叩く＝送るものが無い日も「活動あり」になり、pause を防ぐ
        return {r["id"] for r in self.request("games?select=id")}

    def upsert_game(self, row):
        self.request("games", "POST", [row],
                     prefer="resolution=merge-duplicates,return=minimal")

    def replace_moves(self, game_id, rows):
        # 再解析で手の中身が変わっていても古い行が残らないよう、消してから入れる
        self.request(f"moves?game_id=eq.{game_id}", "DELETE", prefer="return=minimal")
        if rows:
            self.request("moves", "POST", rows, prefer="return=minimal")

    def push_game(self, game, rows):
        """games → moves の順に入れる（moves は games を参照しているので逆にできない）。
        🚨 moves で失敗したら games の行も消す。残すと次回「もう向こうにある」と見なされて
        二度と送られず、一覧には出るのに悪手が一つも無い局として静かに欠ける"""
        self.upsert_game(game)
        try:
            self.replace_moves(game["id"], rows)
        except DbError:
            try:
                self.request(f"games?id=eq.{game['id']}", "DELETE", prefer="return=minimal")
            except DbError:
                pass  # 消せなくても元のエラーのほうを報告する
            raise


# ── JSON → 行 ────────────────────────────────────────────────────────────

def played_at(header):
    """'2026/09/18 11:30:22' → ISO 8601（JST）。書式が違えば None"""
    s = header.get("開始日時", "")
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=JST).isoformat()
        except ValueError:
            pass
    return None


def winner(moves):
    last = moves[-1] if moves else None
    if not last or not last.get("terminal"):
        return None
    k = last["kif"]
    if any(k.startswith(t) for t in LOSER_ENDS):
        return "w" if last["side"] == "b" else "b"
    if any(k.startswith(t) for t in WINNER_ENDS):
        return last["side"]
    return None  # 千日手・持将棋・中断


def to_rows(game_id, data):
    moves = data["moves"]
    played = [m for m in moves if not m["terminal"]]
    ei = data.get("engineInfo", {})
    pa = played_at(data["header"])
    if pa is None:
        raise ValueError(f"開始日時が読めません: {data['header'].get('開始日時')!r}")
    game = {
        "id": game_id,
        "played_at": pa,
        "sente": data["sente"], "gote": data["gote"],
        "sente_rank": data.get("senteRank") or None,
        "gote_rank": data.get("goteRank") or None,
        "time_control": data.get("timeControl"),
        "moves_count": len(played),
        "terminal": moves[-1]["kif"] if moves and moves[-1]["terminal"] else None,
        "winner": winner(moves),
        "avg_loss_b": data["avgLoss"]["b"], "avg_loss_w": data["avgLoss"]["w"],
        "engine": ei.get("engine"), "eval_mode": ei.get("evalMode"),
        "eval_file": ei.get("evalFile"), "movetime": ei.get("movetime"),
        "data": data,
        # 原本。out/*.json は解析結果（派生物）なので、これが無いと Mac 以外に KIF が残らない
        "kif": kif_text(game_id),
    }
    positions = data["positions"]
    rows = []
    for m in moves:
        rows.append({
            "game_id": game_id, "n": m["n"], "side": m["side"], "kif": m["kif"],
            "usi": m.get("usi"),
            # positions[n] は「n手目を指した後」の局面。終局手には局面が無い
            "cp": positions[m["n"]]["cp"] if not m["terminal"] and m["n"] < len(positions) else None,
            "loss": m.get("loss"), "grade": m.get("grade", ""),
        })
    return game, rows


def kif_text(game_id):
    p = GAMES / f"{game_id}.kif"
    return p.read_text(encoding="utf-8") if p.exists() else None


def local_games():
    """out/*.json のうち解析結果のもの（slack_state.json などを除く）"""
    found = {}
    for p in sorted(OUT.glob("*.json")):
        if not re.fullmatch(r"\d{8}_\d{4}", p.stem):
            continue
        found[p.stem] = p
    return found


# ── 本体 ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonfiles", nargs="*", help="送るJSON。省略すると out/ の未送信分")
    ap.add_argument("--all", action="store_true", help="out/ の全部を送り直す")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        db = Db()
        push(db, args)
    except DbError as e:
        sys.exit(f"✗ {e}")


def push(db, args):
    if args.jsonfiles:
        targets = {Path(f).stem: Path(f) for f in args.jsonfiles}
    else:
        targets = local_games()
        if not args.all:
            remote = db.remote_ids()
            targets = {k: v for k, v in targets.items() if k not in remote}
            print(f"Supabase: {len(remote)}局  未送信: {len(targets)}局")

    if not targets:
        print("送るものはありません")
        return

    for game_id, path in targets.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        game, rows = to_rows(game_id, data)
        label = f"{game_id}  ▲{game['sente']} △{game['gote']}  {len(rows)}手"
        if args.dry_run:
            print(f"  (dry-run) {label}")
            continue
        db.push_game(game, rows)
        print(f"  送信 {label}")


if __name__ == "__main__":
    main()
