"""解析済みJSON（out/*.json）を横断して、自分の指し手の傾向をまとめる。

    python3 stats.py --me christel09
    python3 stats.py --me christel09 --by timeControl   # 持ち時間別
    python3 stats.py --me christel09 --by side          # 先手/後手別

エンジンは動かさない。export.py が書いたJSONを読むだけ。

🚨 ものさしが違う数字を混ぜない
平均損失は「どのエンジンで・どの評価モードで・何msで読んだか」に丸ごと依存する。
classical評価の解析とNNUEの解析を同じ折れ線に並べると、腕前が変わっていないのに
上下したように見える。export.py が engineInfo を書き残しているので、
ここではその組み合わせごとに束を分け、混ざっていれば警告を出す。
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DUBIOUS, MISTAKE, BLUNDER = 150, 300, 500

# 終局手を「指した側」から見た勝敗
LOSE = ("投了", "切れ負け", "反則負け", "詰み")
WIN = ("反則勝ち", "入玉勝ち")
DRAW = ("千日手", "持将棋")


def ruler(data):
    """このJSONを作ったときの『ものさし』。これが違う数字は比べられない。"""
    e = data.get("engineInfo")
    if not e:
        # engineInfo を書く前に作ったJSON。何で測ったか分からない
        return ("(出自不明)", "", "", 0, 0)
    return (e.get("engine", ""), e.get("evalMode", ""), e.get("evalFile", ""),
            e.get("movetime", 0), e.get("clamp", 0))


def ruler_label(r):
    engine, mode, ef, mt, clamp = r
    s = f"{engine} / {mode}"
    if ef:
        s += f" ({ef})"
    return s + f" / {mt}ms / clamp {clamp}"


def outcome(data, my_side):
    """自分から見た勝敗。'○' '●' '△' '－'（不明）。"""
    term = next((m for m in data["moves"] if m.get("terminal")), None)
    if term is None:
        return "－"
    kif, side = term["kif"], term["side"]
    if any(kif.startswith(t) for t in DRAW):
        return "△"
    if any(kif.startswith(t) for t in LOSE):
        return "●" if side == my_side else "○"
    if any(kif.startswith(t) for t in WIN):
        return "○" if side == my_side else "●"
    return "－"


def load(paths, me):
    games = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  読めないので飛ばす: {p.name} ({e})")
            continue
        if "moves" not in data:
            continue
        side = "b" if data.get("sente") == me else "w" if data.get("gote") == me else None
        if side is None:
            continue
        losses = [m["loss"] for m in data["moves"]
                  if not m.get("terminal") and m["side"] == side]
        if not losses:
            continue
        games.append({
            "file": p.name,
            "date": data.get("date", ""),
            "side": side,
            "opp": data.get("gote" if side == "b" else "sente", "?"),
            "oppRank": data.get("goteRank" if side == "b" else "senteRank", ""),
            "timeControl": data.get("timeControl", ""),
            "plies": len(losses),
            "losses": losses,
            "result": outcome(data, side),
            "ruler": ruler(data),
        })
    games.sort(key=lambda g: g["date"])
    return games


def counts(losses):
    return (sum(1 for l in losses if DUBIOUS <= l < MISTAKE),
            sum(1 for l in losses if MISTAKE <= l < BLUNDER),
            sum(1 for l in losses if l >= BLUNDER))


def agg(games):
    """束をまとめる。1局ごとの平均をさらに平均するのではなく、全手をならす。

    手数の違う将棋を平均の平均で足すと、短い将棋の1手が長い将棋の1手より
    重くなる。持ち時間別に比べるときにこれが効いてくる。
    """
    losses = [l for g in games for l in g["losses"]]
    d, m, b = counts(losses)
    n = len(losses)
    return {
        "games": len(games), "plies": n,
        "avg": sum(losses) / n if n else 0,
        "dubious": d, "mistake": m, "blunder": b,
        # 手数がばらつくので、率は「100手あたり」に直して比べる
        "badPer100": (d + m + b) * 100 / n if n else 0,
        "blunderPer100": b * 100 / n if n else 0,
        "record": Counter(g["result"] for g in games),
    }


def bar(avg, width=24, full=150):
    k = min(width, round(avg / full * width))
    return "█" * k + "·" * (width - k)


def line(label, a):
    r = a["record"]
    return (f"  {label:<22} {a['games']:>3}局 {a['plies']:>4}手  "
            f"平均損失 {a['avg']:>5.0f} {bar(a['avg'])}  "
            f"悪手/100手 {a['badPer100']:>4.1f}（大 {a['blunderPer100']:>3.1f}）  "
            f"{r['○']}勝{r['●']}敗{r['△']}分")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--me", help="自分のプレイヤー名（省略時は全局に出てくる名前を推測）")
    ap.add_argument("--dir", default="out", help="解析JSONの置き場")
    ap.add_argument("--by", choices=["timeControl", "side", "opp"],
                    help="この項目でまとめ直す")
    args = ap.parse_args()

    paths = sorted(Path(args.dir).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.dir}/ に解析JSONがありません。先に export.py を回してください")

    me = args.me
    if not me:
        # 全部の対局に出てくる名前が1つだけなら、それが自分
        names = None
        for p in paths:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            s = {d.get("sente"), d.get("gote")} - {None}
            names = s if names is None else names & s
        cand = sorted(n for n in (names or []) if n)
        if len(cand) != 1:
            raise SystemExit("自分の名前を決められません。--me で指定してください"
                             + (f"（候補: {', '.join(cand)}）" if cand else ""))
        me = cand[0]
        print(f"（--me 省略。全局に出てくる {me} を自分とみなします）\n")

    games = load(paths, me)
    if not games:
        raise SystemExit(f"{me} の対局が {args.dir}/ に見つかりません")

    # ── ものさしの点検 ─────────────────────────────────────────
    by_ruler = defaultdict(list)
    for g in games:
        by_ruler[g["ruler"]].append(g)
    if len(by_ruler) > 1:
        print("🚨 解析条件の違うJSONが混ざっています。平均損失を横に並べても比べられません。")
        for r, gs in by_ruler.items():
            print(f"   {len(gs):>2}局  {ruler_label(r)}")
        print("   → 揃えるには、同じ条件で export.py を回し直すこと。"
              "以下は条件ごとに分けて出します。\n")

    print(f"■ {me}  全{len(games)}局\n")
    print(f"  {'日時':<17} {'手番':<4} {'相手':<16} {'手数':>4} {'平均損失':>6} "
          f"{'?!':>3}{'?':>3}{'??':>3}  結果")
    print("  " + "-" * 78)
    for g in games:
        d, m, b = counts(g["losses"])
        avg = sum(g["losses"]) / len(g["losses"])
        print(f"  {g['date'][:16]:<17} {'▲先手' if g['side']=='b' else '△後手':<4} "
              f"{g['opp'][:14]:<16} {g['plies']:>4} {avg:>6.0f} "
              f"{d:>3}{m:>3}{b:>3}  {g['result']}")

    for r, gs in sorted(by_ruler.items(), key=lambda kv: -len(kv[1])):
        print(f"\n■ まとめ  [{ruler_label(r)}]")
        print(line("全体", agg(gs)))
        if args.by:
            groups = defaultdict(list)
            for g in gs:
                key = {"side": "▲先手" if g["side"] == "b" else "△後手"}.get(
                    args.by, str(g[args.by]))
                groups[key].append(g)
            print()
            for key in sorted(groups):
                print(line(key or "(不明)", agg(groups[key])))

    print("\n  平均損失: 1手あたり何点損したか。小さいほど良い（棒は150点で満杯）")
    print("  ?! 疑問手150〜 / ? 悪手300〜 / ?? 大悪手500〜")


if __name__ == "__main__":
    main()
