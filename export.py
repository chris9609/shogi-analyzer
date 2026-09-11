"""棋譜を解析して、ビューア用のJSONを吐く。

    python3 export.py games/20260901_1056.kif -o out/20260901_1056.json

各局面について 盤面(SFEN)・評価値・損失・最善手・読み筋 をまとめる。
評価値はすべて先手視点。
"""

import argparse
import json
import sys
import time
from pathlib import Path

import kif
from engine import Engine, MATE_SCORE, default_engine

CLAMP = 2000
DUBIOUS, MISTAKE, BLUNDER = 150, 300, 500


def clamp(cp):
    return 0 if cp is None else max(-CLAMP, min(CLAMP, cp))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kiffile")
    ap.add_argument("-o", "--out")
    ap.add_argument("--movetime", type=int, default=1500)
    ap.add_argument("--pv", type=int, default=12)
    ap.add_argument("--engine", default=default_engine(),
                    help="USIエンジンのパス（既定: engines/yaneuraou-nnue があればそれ、無ければ fairy-stockfish）")
    ap.add_argument("--eval-file", help="評価関数のパス（既定: やねうら王は engines/*/nn.bin、Fairy は nnue/*.nnue を自動検出）")
    ap.add_argument("--classical", action="store_true",
                    help="NNUEを使わず classical 評価で解析する")
    ap.add_argument("--multipv", type=int, default=3,
                    help="候補手を何手まで残すか（既定3）")
    args = ap.parse_args()

    header, moves = kif.parse(Path(args.kiffile).read_text(encoding="utf-8"))
    usi = kif.usi_moves(moves)

    positions = []
    with Engine(args.engine, eval_file="" if args.classical else args.eval_file) as eng:
        print(f"エンジン: {eng.describe()}  movetime {args.movetime}ms "
              f"MultiPV {args.multipv}", file=sys.stderr)
        t0 = time.time()
        for i in range(len(usi) + 1):
            prefix = usi[:i]

            # 盤面(SFEN)を取る。ついでに手数が合っているかを検算する
            _, sfen = eng.board(prefix)
            if sfen is None or int(sfen.rsplit(" ", 1)[1]) != i + 1:
                sys.exit(f"✗ {i}手目で局面が食い違う。verify.py で確認すること")

            r = eng.analyse(prefix, args.movetime, multipv=args.multipv)
            cp = (r["score"] or 0) * (1 if i % 2 == 0 else -1)
            positions.append({
                "ply": i,
                "sfen": sfen,
                "cp": cp,
                "best": r["best"],
                "pv": r["pv"][: args.pv],
                "depth": r["depth"],
                # 候補手。cp は局面の cp と同じく先手視点に揃える。
                # エンジンは手番側視点で返すので、ここで符号を合わせておかないと
                # 同じ画面の中で向きの違う数字が並ぶ
                "candidates": [
                    {"move": c["move"], "cp": c["cp"] * (1 if i % 2 == 0 else -1),
                     "pv": c["pv"][: args.pv]}
                    for c in r["candidates"]
                ],
            })
            print(f"\r  {i}/{len(usi)}", end="", file=sys.stderr)
        print(f"\r  解析完了 ({time.time() - t0:.0f}秒)      ", file=sys.stderr)

    # 各手の損失を出す
    out_moves = []
    for mv in moves:
        n = mv["n"]
        if mv["terminal"]:
            out_moves.append({"n": n, "kif": mv["terminal"], "terminal": True,
                              "side": mv["side"]})
            continue
        b, a = clamp(positions[n - 1]["cp"]), clamp(positions[n]["cp"])
        loss = max(0, (b - a) if mv["side"] == "b" else (a - b))
        out_moves.append({
            "n": n, "kif": mv["kif"], "usi": mv["usi"], "side": mv["side"],
            "loss": loss,
            "grade": "blunder" if loss >= BLUNDER else "mistake" if loss >= MISTAKE
                     else "dubious" if loss >= DUBIOUS else "",
            "terminal": False,
        })

    def avg(side):
        ls = [m["loss"] for m in out_moves if not m["terminal"] and m["side"] == side]
        return round(sum(ls) / len(ls)) if ls else 0

    data = {
        "header": header,
        "sente": header.get("先手", "?"), "gote": header.get("後手", "?"),
        "senteRank": header.get("先手段級", ""), "goteRank": header.get("後手段級", ""),
        "date": header.get("開始日時", ""),
        "timeControl": f"{header.get('持ち時間','')}/秒読み{header.get('秒読み','')}",
        "mateScore": MATE_SCORE,
        "clamp": CLAMP,
        # 🔑 評価値は「どのものさしで測ったか」で意味が変わる。エンジン・評価モード・
        # 探索時間が違うJSONを混ぜて平均損失を並べると、腕前が変わっていないのに
        # 上下したように見える。stats.py はここを見て束を分ける
        "engineInfo": {
            "engine": eng.name,
            "evalMode": eng.eval_mode,
            # 引数ではなく、エンジンが実際に積んだファイルを記録する
            "evalFile": eng.eval_label(),
            "movetime": args.movetime,
            "multipv": args.multipv,
            "clamp": CLAMP,
            "thresholds": {"dubious": DUBIOUS, "mistake": MISTAKE, "blunder": BLUNDER},
        },
        "avgLoss": {"b": avg("b"), "w": avg("w")},
        "moves": out_moves,
        "positions": positions,
    }

    out = Path(args.out) if args.out else \
        Path("out") / (Path(args.kiffile).stem + ".json")
    out.parent.mkdir(exist_ok=True, parents=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"書き出し: {out}  ({out.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
