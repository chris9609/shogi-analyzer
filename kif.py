"""将棋ウォーズのKIF棋譜を読んで、USI形式の指し手列に変換する。

将棋ウォーズのKIFは移動元が必ず括弧で書かれている（例: ６八銀(79)）ので、
盤面を再現しなくても機械的にUSIへ変換できる。依存ライブラリなし。
"""

import re

# 全角数字 → 半角
ZEN = str.maketrans("１２３４５６７８９", "123456789")
# 漢数字の段 → USIのランク
RANK = dict(zip("一二三四五六七八九", "abcdefghi"))
# 持ち駒を打つときの駒種
DROP = {"歩": "P", "香": "L", "桂": "N", "銀": "S", "金": "G", "角": "B", "飛": "R"}
# 成駒の駒名（頭に「成」が付くものは駒名であって、成る動作ではない）
PROMOTED_NAMES = ("成銀", "成桂", "成香", "と", "馬", "龍", "竜")

TERMINAL = ("投了", "中断", "千日手", "持将棋", "切れ負け", "反則勝ち", "反則負け", "入玉勝ち", "詰み")

HEADER_RE = re.compile(r"^(.+?)：(.*)$")
MOVE_RE = re.compile(r"^\s*(\d+)\s+(\S+)")


class KifError(ValueError):
    pass


def parse(text):
    """KIF文字列を (header: dict, moves: list[dict]) に分解する。

    moves の各要素:
        n        手数（1始まり）
        kif      元のKIF表記
        usi      USI表記。終局手（投了など）は None
        terminal 終局理由の文字列。通常手は None
        side     "b"（先手）または "w"（後手）
    """
    header, moves = {}, []
    prev_dest = None  # 「同」の解決用

    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or line.startswith("手数---"):
            continue

        m = HEADER_RE.match(line)
        if m and not MOVE_RE.match(line):
            header[m.group(1).strip()] = m.group(2).strip()
            continue

        m = MOVE_RE.match(line)
        if not m:
            continue
        n, body = int(m.group(1)), m.group(2)

        entry = {"n": n, "kif": body, "usi": None, "terminal": None,
                 "side": "b" if n % 2 else "w"}

        if any(body.startswith(t) for t in TERMINAL):
            entry["terminal"] = body
            moves.append(entry)
            continue

        usi, prev_dest = _to_usi(body, prev_dest)
        entry["usi"] = usi
        moves.append(entry)

    return header, moves


def _to_usi(body, prev_dest):
    """1手のKIF表記をUSIに変換し、(usi, この手の移動先) を返す。"""
    # 移動先
    if body.startswith("同"):
        if prev_dest is None:
            raise KifError(f"「同」が最初の手に現れた: {body}")
        dest = prev_dest
        rest = body.lstrip("同").lstrip("　 ")
    else:
        if len(body) < 2:
            raise KifError(f"読めない指し手: {body}")
        f, r = body[0].translate(ZEN), body[1]
        if f not in "123456789" or r not in RANK:
            raise KifError(f"読めない移動先: {body}")
        dest = f + RANK[r]
        rest = body[2:]

    # 持ち駒を打つ手
    if rest.endswith("打"):
        piece = rest[:-1]
        if piece not in DROP:
            raise KifError(f"打てない駒: {body}")
        return f"{DROP[piece]}*{dest}", dest

    # 移動元 (79) を取り出す
    m = re.search(r"\((\d)(\d)\)$", rest)
    if not m:
        raise KifError(f"移動元が読めない: {body}")
    src = m.group(1) + "abcdefghi"[int(m.group(2)) - 1]
    piece_part = rest[: m.start()]

    # 成るかどうか。「不成」は成らない。「成銀」等は駒名なので成る動作ではない
    promote = (
        piece_part.endswith("成")
        and not piece_part.endswith("不成")
        and piece_part not in PROMOTED_NAMES
    )

    return f"{src}{dest}{'+' if promote else ''}", dest


def usi_moves(moves):
    """終局手を除いた USI 指し手のリスト。"""
    return [m["usi"] for m in moves if m["usi"]]


if __name__ == "__main__":
    import sys

    header, moves = parse(open(sys.argv[1], encoding="utf-8").read())
    print(f"先手: {header.get('先手')} ({header.get('先手段級','')})")
    print(f"後手: {header.get('後手')} ({header.get('後手段級','')})")
    print(f"手数: {len(moves)}")
    for mv in moves:
        print(f"  {mv['n']:3} {mv['kif']:<14} {mv['usi'] or '(' + mv['terminal'] + ')'}")
