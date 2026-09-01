"""将棋ウォーズの棋譜を1局まるごと解析する。棋神解析のローカル版。

    python3 analyze.py games/20260830_0023.kif            # 全手を解析
    python3 analyze.py games/xxx.kif --at 34              # 34手目の局面だけ詳しく
    python3 analyze.py games/xxx.kif --movetime 3000      # 1手あたりの探索時間(ms)

評価値はすべて「先手視点」に揃えて表示する（プラス=先手優勢）。
USI の score は手番側視点で返るので、後手番の局面では符号を反転している。
"""

import argparse
import sys
import time

import kif
from engine import Engine, MATE_SCORE

# 評価値の下落がこの幅を超えたら印をつける（単位: センチポーン）
BLUNDER = 500
MISTAKE = 300
DUBIOUS = 150

# 損失を測るときに評価値を丸める上限。
# 詰み(10万点)をそのまま引き算すると損失が9万点になって無意味なので、
# 「もう決まっている局面」は同じ値に潰してから差を取る。
CLAMP = 2000


def clamp(cp):
    """損失計算用に評価値を ±CLAMP に丸める。詰みもここに吸収される。"""
    if cp is None:
        return 0
    return max(-CLAMP, min(CLAMP, cp))


def is_mate(cp):
    return cp is not None and abs(cp) >= MATE_SCORE - 1000


def fmt_score(cp):
    if cp is None:
        return "?"
    if is_mate(cp):
        n = MATE_SCORE - abs(cp)
        return f"▲詰{n}" if cp > 0 else f"△詰{n}"
    return f"{cp:+d}"


def mark(loss):
    if loss is None:
        return "  "
    if loss >= BLUNDER:
        return "??"
    if loss >= MISTAKE:
        return "? "
    if loss >= DUBIOUS:
        return "?!"
    return "  "


def usi_to_ja(u):
    """USI表記をKIF風の座標表記に。駒種は追えないので移動元だけ括弧で添える。"""
    if not u:
        return ""
    kan = "一二三四五六七八九"
    if "*" in u:
        p = {"P": "歩", "L": "香", "N": "桂", "S": "銀",
             "G": "金", "B": "角", "R": "飛"}.get(u[0], u[0])
        return f"{u[2]}{kan[ord(u[3]) - 97]}{p}打"
    dst = f"{u[2]}{kan[ord(u[3]) - 97]}"
    return dst + ("成" if u.endswith("+") else "") + f"({u[0]}{ord(u[1]) - 96})"


def main():
    global CLAMP

    ap = argparse.ArgumentParser()
    ap.add_argument("kiffile")
    ap.add_argument("--movetime", type=int, default=1000, help="1局面あたりの探索時間(ms)")
    ap.add_argument("--at", type=int, help="この手数の局面だけ詳しく見る")
    ap.add_argument("--pv", type=int, default=10, help="読み筋を何手表示するか")
    ap.add_argument("--engine", default="fairy-stockfish")
    ap.add_argument("--clamp", type=int, default=CLAMP,
                    help="損失計算で評価値を丸める上限。これを超えた局面は"
                         "「もう決まっている」とみなして損失を数えない")
    args = ap.parse_args()
    CLAMP = args.clamp

    header, moves = kif.parse(open(args.kiffile, encoding="utf-8").read())
    usi = kif.usi_moves(moves)

    print(f"▲{header.get('先手','?')} ({header.get('先手段級','')})"
          f"  vs  △{header.get('後手','?')} ({header.get('後手段級','')})")
    print(f"{header.get('開始日時','')}  {header.get('持ち時間','')}"
          f"/秒読み{header.get('秒読み','')}  全{len(moves)}手\n")

    with Engine(args.engine) as eng:
        # --at: 指定局面だけ深く読む
        if args.at is not None:
            n = args.at
            if not 0 <= n <= len(usi):
                sys.exit(f"手数は 0〜{len(usi)} の範囲で指定してください")
            r = eng.analyse(usi[:n], args.movetime)
            side = "先手" if n % 2 == 0 else "後手"
            cp = (r["score"] or 0) if n % 2 == 0 else -(r["score"] or 0)
            print(f"{n}手目まで進んだ局面（手番: {side}）")
            print(f"  評価値（先手視点）: {fmt_score(cp)}   depth {r['depth']}")
            print(f"  最善手: {usi_to_ja(r['best'])}  [{r['best']}]")
            print(f"  読み筋: " + " ".join(r["pv"][: args.pv]))
            return

        # 全局面を順に解析
        t0 = time.time()
        evals = []
        for i in range(len(usi) + 1):
            r = eng.analyse(usi[:i], args.movetime)
            # i手目まで指した局面の手番は、iが偶数なら先手
            cp = r["score"] if i % 2 == 0 else -(r["score"] or 0)
            evals.append({"cp": cp, "best": r["best"], "pv": r["pv"]})
            print(f"\r  解析中 {i}/{len(usi)} ...", end="", file=sys.stderr)
        print(f"\r  解析完了 ({time.time()-t0:.0f}秒)          ", file=sys.stderr)

    print(f"{'手':>3} {'指し手':<12} {'評価値':>8} {'損失':>6}    {'最善手':<12} 読み筋")
    print("-" * 100)

    worst = []
    for mv in moves:
        n = mv["n"]
        if mv["terminal"]:
            print(f"{n:>3} {mv['terminal']}")
            continue
        before, after = evals[n - 1], evals[n]
        # 先手の手なら評価値が下がった分、後手の手なら上がった分がその人の損失。
        # 詰み絡みで桁が飛ばないよう、丸めた値どうしで差を取る
        b, a = clamp(before["cp"]), clamp(after["cp"])
        loss = max(0, (b - a) if mv["side"] == "b" else (a - b))
        played = mv["usi"]
        best = before["best"]
        star = "" if played == best else usi_to_ja(best)
        print(f"{n:>3} {mv['kif']:<12} {fmt_score(after['cp']):>8} {loss:>6}{mark(loss)}"
              f"  {star:<12} " + " ".join(before["pv"][: args.pv]))
        if loss >= DUBIOUS:
            worst.append((loss, mv, best))

    print()
    _summary(moves, evals, worst, header)


def _summary(moves, evals, worst, header):
    for side, label, name in (("b", "先手", header.get("先手", "")),
                              ("w", "後手", header.get("後手", ""))):
        losses = []
        for mv in moves:
            if mv["terminal"] or mv["side"] != side:
                continue
            b = clamp(evals[mv["n"] - 1]["cp"])
            a = clamp(evals[mv["n"]]["cp"])
            losses.append(max(0, (b - a) if side == "b" else (a - b)))
        if not losses:
            continue
        print(f"{label} {name}: 平均損失 {sum(losses)/len(losses):>6.0f}   "
              f"疑問手 {sum(1 for l in losses if DUBIOUS <= l < MISTAKE):>2}  "
              f"悪手 {sum(1 for l in losses if MISTAKE <= l < BLUNDER):>2}  "
              f"大悪手 {sum(1 for l in losses if l >= BLUNDER):>2}")

    if worst:
        print("\n■ 響いた手（損失の大きい順）")
        for loss, mv, best in sorted(worst, reverse=True, key=lambda x: x[0])[:5]:
            who = "▲" if mv["side"] == "b" else "△"
            print(f"  {mv['n']:>3}手目 {who}{mv['kif']:<12} -{loss:<6} "
                  f"→ 最善は {usi_to_ja(best)} [{best}]")


if __name__ == "__main__":
    main()
