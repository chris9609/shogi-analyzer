"""KIF→USI 変換が正しいかをエンジン側で検算する。

    python3 verify.py games/xxx.kif
    python3 verify.py games/xxx.kif --at 34    # その局面の盤面も表示する

なぜ必要か:
Stockfish系は `position startpos moves ...` に不正な手が混ざると、
**エラーを出さずにそこまでで適用を打ち切る**。変換にバグがあっても
「それらしい評価値」が返ってくるので、目視では絶対に気づけない。
エンジンが報告する手数が期待値と一致することを確認して初めて、
解析結果を信用できる。

やねうら王は逆に、不正な手を見ると `Illegal move` と言って**プロセスごと終了する**。
どちらの流儀でも「どの手が疑わしいか」を出せるように、エンジンが黙るのも拾う。

新しい棋譜を解析する前にこれを通すこと。
"""

import argparse
import sys

import kif
from engine import Engine, default_engine


def board_at(eng, moves):
    """指定手数までの局面を (盤面テキスト, SFEN) で返す。"""
    return eng.board(moves)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kiffile")
    ap.add_argument("--at", type=int, help="この手数の盤面を表示する")
    ap.add_argument("--engine", default=default_engine(),
                    help="USIエンジンのパス（既定: engines/yaneuraou-nnue があればそれ、無ければ fairy-stockfish）")
    args = ap.parse_args()

    header, moves = kif.parse(open(args.kiffile, encoding="utf-8").read())
    usi = kif.usi_moves(moves)
    print(f"KIF: {len(moves)}手（うち指し手 {len(usi)}手）")

    ok = True
    with Engine(args.engine) as eng:
        seen, dups = {}, []
        for i in range(len(usi) + 1):
            try:
                _, sfen = board_at(eng, usi[:i])
            except RuntimeError:
                # やねうら王は不正な手でプロセスごと落ちる。落ちた直前の手が犯人
                print(f"✗ {i}手目でエンジンが終了した（不正な手を拒否）")
                print(f"  疑わしい手: {moves[i - 1]['kif'] if i else '?'}"
                      f" → {usi[i - 1] if i else '?'}")
                ok = False
                break
            if sfen is None:
                print(f"✗ {i}手目: エンジンからSFENが取れない")
                ok = False
                break
            body, ply = sfen.rsplit(" ", 1)[0], int(sfen.rsplit(" ", 1)[1])
            if ply != i + 1:
                print(f"✗ {i}手目で適用が止まっている"
                      f"（エンジンの手数 {ply} / 期待 {i + 1}）")
                print(f"  疑わしい手: {moves[i - 1]['kif'] if i else '?'}"
                      f" → {usi[i - 1] if i else '?'}")
                ok = False
                break
            if body in seen:
                dups.append((seen[body], i))
            seen[body] = i
        else:
            print(f"✓ 全{len(usi)}手が適用された（最終局面の手数 = {len(usi) + 1}）")
            print(f"✓ 重複局面 {len(dups)}件"
                  + (f" {dups}" if dups else "（千日手なし）"))

        if args.at is not None and ok:
            board, sfen = board_at(eng, usi[: args.at])
            print(f"\n── {args.at}手目まで進んだ局面 ──")
            print(board)
            print(f"Sfen: {sfen}")
            print("\n将棋ウォーズの画面と、駒の配置・持ち駒・手番を突き合わせること。")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
