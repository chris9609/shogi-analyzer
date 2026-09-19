"""Slackの専用チャンネルに貼られたKIFを拾って、保存→検算→解析→HTML化まで回す。

    python3 slack_watch.py                    # 新着を1回見にいく（launchd が毎日19時に叩く）
    python3 slack_watch.py --channel '#メモ'   # 別のチャンネルを見る（試すとき）
    python3 slack_watch.py --dry-run          # 拾うところまで。解析も投稿もしない
    python3 slack_watch.py --since 0          # 記録を無視して、取れる範囲の過去分から見る

iPhoneで棋譜をSlackに貼れば、あとはエンジンのあるこのMacが勝手に解析する、というのが狙い。
返信は出さない。成功なら ✅ を付けるだけで、内容は全対局ページで見る。
**貼り直しが要るとき（棋譜が切れている等）と失敗だけ**は、気づけないと困るのでSlackに書く。

設計の前提（Botの権限が chat:write / channels:history / channels:read / reactions:write しかない）:
  * **本文として貼ること**。Slackがスニペット（添付ファイル）にしたものは files:read が無いので読めない。
    その場合は「本文で貼って」と返す。
  * どこまで処理したかは ✅ のリアクションではなく `out/slack_state.json` の ts で覚える。
    リアクションは人間向けの目印（reactions:read が無いので、こちらからは読み返せない）。
  * 解析は数分かかる。launchd の間隔と重なって二重に走らないよう、実行中はロックを取る。
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import add
import kif

HERE = Path(__file__).parent
OUT = HERE / "out"
STATE = OUT / "slack_state.json"
LOCK = OUT / "slack_watch.lock"
# トークンはこのリポジトリには置かない（公開する前提のリポジトリなので）。
# MCPサーバ（AI秘書）が使っているものを借りる
ENV_FILE = Path.home() / "claude/application/MCP/.env"
DEFAULT_CHANNEL = "#将棋"


def log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


# ── Slack ────────────────────────────────────────────────────────────────

class Slack:
    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        """Slack Web API を叩く。ok=false は例外にする（黙って進むと事故る）。"""
        data = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(
            f"https://slack.com/api/{method}", data=data,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        if not body.get("ok"):
            raise RuntimeError(f"Slack {method}: {body.get('error')}")
        return body

    def channel_id(self, name):
        if not name.startswith("#"):
            return name  # すでにIDが渡されている
        want = name.lstrip("#")
        cursor = None
        while True:
            # private_channel も混ぜると groups:read が要る。Botには無いので公開チャンネルだけ見る
            r = self.call("conversations.list", limit=200, cursor=cursor,
                          exclude_archived="true", types="public_channel")
            for ch in r["channels"]:
                if ch["name"] == want:
                    if not ch.get("is_member"):
                        raise RuntimeError(
                            f"{name} にBotが入っていません。Slackで `/invite @AI秘書` を実行してください")
                    return ch["id"]
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                raise RuntimeError(f"{name} というチャンネルが見つかりません")

    def history(self, channel, oldest, cap=500):
        """oldest より後のメッセージを、古い順にすべて返す。

        1回のAPIで返るのは200件まで。ページを繰らないと、Macを何日か止めた後に
        `--since 0` で追いかけたときに古い方が黙って落ちる。
        落ちたぶんは二度と拾われないので、ここは端折らない。
        """
        msgs, cursor = [], None
        while len(msgs) < cap:
            r = self.call("conversations.history", channel=channel, oldest=oldest,
                          limit=200, cursor=cursor, inclusive="false")
            msgs += r["messages"]
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not r.get("has_more") or not cursor:
                break
        return list(reversed(msgs))  # 古い順に処理する

    def post(self, channel, text, thread_ts=None):
        self.call("chat.postMessage", channel=channel, text=text,
                  thread_ts=thread_ts, unfurl_links="false")

    def react(self, channel, ts, emoji):
        try:
            self.call("reactions.add", channel=channel, timestamp=ts, name=emoji)
        except RuntimeError as e:
            log(f"  リアクションは付けられなかった（{e}）")  # 本筋ではないので握りつぶす


def load_token():
    if os.environ.get("SLACK_BOT_TOKEN"):
        return os.environ["SLACK_BOT_TOKEN"]
    if ENV_FILE.exists():
        m = re.search(r"^SLACK_BOT_TOKEN=(.+)$", ENV_FILE.read_text(), re.M)
        if m:
            return m.group(1).strip().strip("\"'")
    sys.exit(f"Slackのトークンが見つかりません（{ENV_FILE} か環境変数 SLACK_BOT_TOKEN）")


# ── 状態とロック ─────────────────────────────────────────────────────────

def read_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"channels": {}}


def write_state(state):
    OUT.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def take_lock():
    """二重起動を防ぐ。すでに走っているなら None を返す。"""
    OUT.mkdir(exist_ok=True)
    f = open(LOCK, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f  # 開いたままにしておく（閉じるとロックが外れる）


# ── 解析 ─────────────────────────────────────────────────────────────────

def run(cmd):
    """子プロセスを回して (成功したか, 出力) を返す。"""
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def analyse(path, movetime, multipv):
    """検算 → JSON → HTML。失敗したら (None, 理由) を返す。"""
    ok, out = run([sys.executable, "verify.py", str(path)])
    if not ok:
        return None, f"検算に落ちました。KIF→USI変換が怪しいので解析は止めます。\n```{out[-800:]}```"

    js = OUT / (path.stem + ".json")
    ok, out = run([sys.executable, "export.py", str(path), "-o", str(js),
                   "--movetime", str(movetime), "--multipv", str(multipv)])
    if not ok:
        return None, f"解析に失敗しました。\n```{out[-800:]}```"

    ok, out = run([sys.executable, "build.py", str(js)])
    if not ok:
        return None, f"HTMLの書き出しに失敗しました。\n```{out[-800:]}```"
    return js, None


def report(header, moves, js, elapsed):
    """解析結果を人が読める形にする。19時のログに残して、後から追えるようにする。"""
    data = json.loads(js.read_text(encoding="utf-8"))
    played = [m for m in data["moves"] if not m["terminal"]]

    last = moves[-1]
    result = ""
    if last["terminal"] in ("投了", "切れ負け", "反則負け"):
        loser = "▲" + data["sente"] if last["side"] == "b" else "△" + data["gote"]
        winner = "△" + data["gote"] if last["side"] == "b" else "▲" + data["sente"]
        result = f"{winner} の勝ち（{loser} {last['terminal']}）"
    elif last["terminal"]:
        result = last["terminal"]

    def line(side, mark, name):
        ms = [m for m in played if m["side"] == side]
        bad = sum(1 for m in ms if m["grade"] == "blunder")
        mis = sum(1 for m in ms if m["grade"] == "mistake")
        return (f"{mark}{name}  平均損失 {data['avgLoss'][side]}  "
                f"悪手{bad} 疑問手{mis}")

    worst = sorted(played, key=lambda m: -m["loss"])[:3]
    worst_txt = "\n".join(
        f"  {m['n']}手目 {'▲' if m['side'] == 'b' else '△'}{m['kif']}  −{m['loss']}"
        for m in worst if m["loss"] > 0)

    e = data["engineInfo"]
    return (f"*▲{data['sente']}（{data['senteRank']}） vs △{data['gote']}（{data['goteRank']}）*\n"
            f"{data['date']}  全{len(moves)}手  {result}\n\n"
            f"{line('b', '▲', data['sente'])}\n"
            f"{line('w', '△', data['gote'])}\n\n"
            f"大きく損した手:\n{worst_txt or '  なし'}\n\n"
            f"盤で見る（Macで）: `open {OUT / (js.stem + '.html')}`\n"
            f"_{e['engine']} / {e['evalFile']} / {e['movetime']}ms / 解析{elapsed:.0f}秒_")


# ── 本体 ─────────────────────────────────────────────────────────────────

def handle(sl, channel, msg, args):
    """1件のメッセージを処理して、次回も見るべきかを返す。

        "done"  … 決着がついた（取り込んだ / 棋譜ではない / 貼り直してもらうしかない）。
                  次回からは見ない
        "retry" … 今回たまたま駄目だっただけかもしれない（エンジンが落ちた、通信が切れた）。
                  **次回もう一度見る**

    ここを一律 "done" にすると、19時に1局目でエンジンが落ちただけで、その対局が
    games/ に残らないまま二度と拾われなくなる。指した記録が静かに消えるのが一番まずい。
    """
    ts, text = msg["ts"], msg.get("text", "")

    # Slackがスニペットにしてしまった棋譜は、files:read が無いので読めない
    if msg.get("files") and not add.looks_like_kif(text):
        log("  添付ファイルだった → 本文で貼るよう返す")
        sl.post(channel, "棋譜が添付ファイルになっています。読めないので、本文として貼ってください。"
                         "（iPhoneのSlackならそのまま貼れば本文になります）", thread_ts=ts)
        sl.react(channel, ts, "warning")
        return "done"          # このメッセージは何度見ても読めない。貼り直し待ち

    if not add.looks_like_kif(text):
        return "done"          # ただの雑談

    try:
        header, moves = kif.parse(text)
    except kif.KifError as e:
        sl.post(channel, f"KIFとして読めませんでした: {e}", thread_ts=ts)
        sl.react(channel, ts, "x")
        return "done"

    # 長い棋譜はSlackが2通に割ることがある。途中まででも「それらしい」解析結果が
    # 出てしまうので、終局の記録が無いものは受け取らない
    if not moves or not moves[-1]["terminal"]:
        log("  終局手が無い → 途中で切れている疑い")
        sl.post(channel, "棋譜が途中で切れているようです（終局の行がありません）。"
                         "もう一度、全体を貼り直してください。", thread_ts=ts)
        sl.react(channel, ts, "warning")
        return "done"

    # --dry-run はここで止める。games/ に書いてしまうと「拾うだけ」ではなくなる
    if args.dry_run:
        p = add.game_path(header)
        log(f"  --dry-run: {p.name}（全{len(moves)}手）として"
            f"{'保存済み' if p.exists() else '保存'} → 解析する")
        return "done"

    try:
        path, header, moves = add.save(text)
        log(f"  保存: {path.name}  全{len(moves)}手")
    except add.DuplicateGame as e:
        if not e.same:
            sl.post(channel, f"同じ開始日時で中身の違う棋譜があります: `{e.path.name}`\n"
                             "手で確認してください。", thread_ts=ts)
            sl.react(channel, ts, "warning")
            return "done"
        path = e.path
        # 棋譜はあるが解析結果が無い ＝ 前回ここで落ちている。解析だけやり直す
        if (OUT / (path.stem + ".json")).exists():
            log(f"  解析済み: {path.name}")
            sl.react(channel, ts, "white_check_mark")
            return "done"
        log(f"  棋譜は保存済みだが解析結果が無い: {path.name} → 解析からやり直す")
    except ValueError as e:
        sl.post(channel, f"取り込めませんでした: {e}", thread_ts=ts)
        sl.react(channel, ts, "x")
        return "done"

    sl.react(channel, ts, "hourglass_flowing_sand")
    t0 = time.time()
    js, err = analyse(path, args.movetime, args.multipv)
    if err:
        log(f"  失敗: {err.splitlines()[0]}")
        sl.post(channel, err, thread_ts=ts)
        sl.react(channel, ts, "x")
        # 検算落ちは何度やっても同じ（変換のバグ）。解析の失敗は、
        # エンジンが落ちただけということがあるので次回もう一度試す
        return "done" if "検算" in err else "retry"

    # 成功したことは ✅ だけで伝える。中身は全対局ページ（今後作る）で見るものなので、
    # Slackに毎日3通の解析結果を流しても読まない。ログには残す
    sl.react(channel, ts, "white_check_mark")
    log(f"  完了: {js.name}（{time.time() - t0:.0f}秒）\n"
        + textwrap.indent(report(header, moves, js, time.time() - t0), "    "))
    return "done"


MAX_RETRY = 3


def scrub(text):
    """万一トークンらしき文字列が混ざっても、Slackに垂れ流さない。"""
    return re.sub(r"xox[baprs]-[A-Za-z0-9-]+", "xox***", str(text))


def channel_state(state, name):
    """このチャンネルの進み具合を取り出す（古い形式からの移行つき）。

        oldest  … ここまでは全部片付いた、という透かし
        done    … 透かしより後ろで、もう片付いたもの
        retries … 失敗して次回もう一度試すもの（回数）
    """
    ch = state["channels"].get(name, {})
    if isinstance(ch, str):            # 旧形式（透かしの文字列だけ）
        ch = {"oldest": ch}
    ch.setdefault("oldest", "0")
    ch.setdefault("done", [])
    ch.setdefault("retries", {})
    state["channels"][name] = ch
    return ch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=os.environ.get("SHOGI_SLACK_CHANNEL", DEFAULT_CHANNEL))
    ap.add_argument("--movetime", type=int, default=10000,
                    help="1局面あたりの思考時間ms（既定10000。1日1回しか回さないので長く取る）")
    ap.add_argument("--multipv", type=int, default=1,
                    help="候補手の数（既定1。ビューアは1位の読み筋しか使わない）")
    ap.add_argument("--dry-run", action="store_true", help="拾うだけで解析も投稿もしない")
    ap.add_argument("--since", help="この ts より後を見る（'0' で取れる範囲の全部）")
    args = ap.parse_args()

    lock = take_lock()
    if lock is None:
        log("すでに実行中なので何もしない")
        return

    sl = Slack(load_token())
    channel = sl.channel_id(args.channel)
    state = read_state()
    ch = channel_state(state, args.channel)
    oldest = args.since if args.since is not None else ch["oldest"]

    msgs = sl.history(channel, oldest)
    log(f"{args.channel} の新着 {len(msgs)}件（{oldest} 以降）")
    done = set(ch["done"])

    for msg in msgs:
        ts = msg["ts"]
        if ts in done:
            continue
        # Bot（自分）の発言は見ない。無限ループの元
        if msg.get("bot_id") or msg.get("subtype") in ("bot_message", "channel_join"):
            done.add(ts)
            continue

        log(f"  {ts}: {msg.get('text','')[:40]!r}")
        try:
            status = handle(sl, channel, msg, args)
        except Exception as e:                     # 1件の失敗で残りを止めない
            log(f"  例外: {e!r}")
            status = "retry"
            try:
                sl.post(channel, f"処理中にエラーが出ました: `{scrub(e)}`", thread_ts=ts)
            except Exception:
                pass                               # Slackにすら言えないなら、ログだけ

        if status == "retry":
            n = ch["retries"].get(ts, 0) + 1
            ch["retries"][ts] = n
            if n < MAX_RETRY:
                log(f"  次回もう一度試す（{n}/{MAX_RETRY}回目）")
            else:
                # 何度やっても駄目なものを永久に抱え込むと、翌日以降の解析も止まる
                log(f"  {MAX_RETRY}回試して駄目だったので諦める")
                sl.post(channel, f"{MAX_RETRY}回試しましたが解析できませんでした。"
                                 "手で `python3 export.py` を回して確かめてください。", thread_ts=ts)
                sl.react(channel, ts, "x")
                status = "done"

        if status == "done":
            done.add(ts)
            ch["retries"].pop(ts, None)

        if not args.dry_run:
            ch["done"] = sorted(done)
            write_state(state)

    # 透かしは「先頭から連続して片付いたところ」までしか進めない。
    # 失敗して再試行待ちのものを飛び越すと、その対局が二度と拾われなくなる
    for msg in msgs:
        if msg["ts"] in done:
            ch["oldest"] = msg["ts"]
            done.discard(msg["ts"])
        else:
            break
    ch["done"] = sorted(done)

    if not args.dry_run:
        write_state(state)
    if ch["retries"]:
        log(f"次回もう一度試すもの: {len(ch['retries'])}件")

    # 解析済みの局を Supabase へ送る（公開ページ docs/ が読む）。
    # 新着が無い日も走らせる。sync.py は毎回 Supabase の一覧を読むので、
    # それが「活動あり」になって無料枠の pause（7日無活動）を防ぐ。
    # 送信に失敗しても透かしは戻さない。棋譜と解析結果は手元（games/ out/）に残っていて、
    # sync.py は「向こうに無いもの」を毎回送り直すので、翌日の実行で自然に追いつく
    if not args.dry_run:
        ok, out = run([sys.executable, "sync.py"])
        log(("Supabase: " if ok else "Supabase 送信に失敗: ") + out.strip().replace("\n", " / ")[-300:])


if __name__ == "__main__":
    main()
