"""序盤の玉・金・銀の配置から、先手・後手それぞれの囲いを判定する。

    python3 castle.py out/*.json       # 各局の判定結果を並べて目で確かめる

解析結果（out/*.json）の positions には各局面の SFEN が入っているので、盤面はそこから読む。
後手は盤を180度回して先手の形として見る（筋も段も反転）。

判定: 序盤 WINDOW 手までの各局面を見て、最後に当てはまった囲いを採る。
美濃→高美濃→銀冠、舟囲い→穴熊のような組み替えは後の形が勝ち、
組んだあと攻められて崩れても、崩れる前の形が残る。
どれにも当てはまらず、玉が5〜6筋の下段（5八・5九・6八・6九）に居れば「居玉」、それ以外は「その他」。
6八玉で急戦に来る居飛車が多いので、居玉に含める。
"""

WINDOW = 60  # 先手・後手合わせての手数

# (名前, 玉の居場所, 要る駒)。上から順に見るので、細かい形を先に置く。
# マスは先手から見た「筋段」（"28" = ２八）。
PATTERNS = [
    ("穴熊", {"99", "19"}, {}),
    ("銀冠", {"28"}, {"S": {"27"}, "G": {"38"}}),
    ("銀冠", {"88"}, {"S": {"87"}, "G": {"78"}}),
    ("矢倉", {"88"}, {"S": {"77"}, "G": {"78"}}),
    ("高美濃", {"28"}, {"S": {"38"}, "G": {"47"}}),
    ("美濃", {"28", "39"}, {"S": {"38"}, "G": {"49"}}),
    ("左美濃", {"88", "87"}, {"S": {"78"}, "G": {"69"}}),
    ("金無双", {"38"}, {"S": {"28"}, "G": {"48"}}),
    ("舟囲い", {"78"}, {}),
]
IGYOKU = "居玉"
IGYOKU_SQ = {"58", "59", "68", "69"}
OTHER = "その他"


def pieces(sfen, side):
    """SFEN から side の玉・金・銀の居場所を {"K": {"59"}, ...} で返す（先手視点）。"""
    out = {"K": set(), "G": set(), "S": set()}
    for r, row in enumerate(sfen.split()[0].split("/"), start=1):
        f, promoted = 9, False
        for c in row:
            if c == "+":
                promoted = True
                continue
            if c.isdigit():
                f -= int(c)
                continue
            # 成銀は銀として数えない
            if not promoted and c.upper() in out and c.isupper() == (side == "b"):
                sq = f"{f}{r}" if side == "b" else f"{10 - f}{10 - r}"
                out[c.upper()].add(sq)
            f, promoted = f - 1, False
    return out


def match(p):
    for name, king, need in PATTERNS:
        if p["K"] & king and all(p[k] & sqs for k, sqs in need.items()):
            return name
    return None


def classify(sfens):
    """各局面の SFEN の列（positions[n]["sfen"]）から {"b": 囲い, "w": 囲い} を返す。"""
    window = sfens[:WINDOW + 1]
    result = {}
    for side in "bw":
        found = None
        for sfen in window:
            found = match(pieces(sfen, side)) or found
        if not found:
            found = IGYOKU if window and pieces(window[-1], side)["K"] & IGYOKU_SQ else OTHER
        result[side] = found
    return result


if __name__ == "__main__":
    import json
    import sys
    from collections import Counter
    from pathlib import Path

    count = Counter()
    for path in sys.argv[1:]:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if "positions" not in d:
            continue
        r = classify([p["sfen"] for p in d["positions"]])
        count.update(r.values())
        print(f"{Path(path).stem}  ▲{d['sente']:<16} {r['b']:<6}  △{d['gote']:<16} {r['w']}")
    print("\n" + "  ".join(f"{k} {n}" for k, n in count.most_common()))
