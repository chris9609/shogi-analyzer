"""序盤の飛車の位置から、先手・後手それぞれの戦型（居飛車 / 何間飛車）を判定する。

    python3 opening.py games/*.kif      # 各局の判定結果を並べて目で確かめる

KIFには戦型タグが入っていないので自前で出す。盤面は再現せず、飛車1枚の
現在地だけを追う（USIの移動元が飛車の居場所と一致したら飛車が動いた手）。

判定: 序盤 WINDOW 手以内に、飛車が自陣（先手なら7〜9段。浮き飛車の横移動は数えない）の5〜8筋に
居たことがあれば振り飛車。何間かは最後に居た筋で決める（四間→三間の転回を拾う）。
一度も振らなければ居飛車。3筋（袖飛車）や4筋（右四間）は居飛車に数える。
"""

import kif

WINDOW = 40  # 先手・後手合わせての手数

# 先手から見た筋 → 戦型
FURI = {8: "向かい飛車", 7: "三間飛車", 6: "四間飛車", 5: "中飛車"}
IBISHA = "居飛車"

START = {"b": "2h", "w": "8b"}
HOME_RANKS = {"b": "ghi", "w": "abc"}


def classify(moves):
    """kif.parse の moves から {"b": 戦型, "w": 戦型} を返す。"""
    rook = dict(START)
    furi = {"b": None, "w": None}  # 最後に振っていた筋（先手視点）

    for mv in moves[:WINDOW]:
        usi = mv["usi"]
        if not usi or "*" in usi:
            continue
        src, dest = usi[:2], usi[2:4]
        side = mv["side"]
        other = "w" if side == "b" else "b"
        if rook[other] == dest:
            rook[other] = None  # 取られた。以後は追わない
        if rook[side] != src:
            continue
        rook[side] = dest
        file, rank = int(dest[0]), dest[1]
        if side == "w":
            file = 10 - file
        if rank in HOME_RANKS[side] and file in FURI:
            furi[side] = file

    return {s: FURI[f] if f else IBISHA for s, f in furi.items()}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    for path in sys.argv[1:]:
        header, moves = kif.parse(Path(path).read_text(encoding="utf-8"))
        r = classify(moves)
        print(f"{Path(path).stem}  ▲{header.get('先手', '?'):<16} {r['b']:<6}"
              f"  △{header.get('後手', '?'):<16} {r['w']}")
