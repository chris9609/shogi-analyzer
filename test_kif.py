"""kif.py の回帰テスト。エンジン不要。

    python3 test_kif.py

「同」「不成」「成駒名」は実棋譜にたまたま出てこなかった表記なので、
ここで固定しておく。とくに「同」は、実棋譜を機械的に「同」表記へ
書き換えて、元の棋譜とUSIが一致するかを見ている（手で作った期待値より
取り違えにくい）。
"""

import sys
from pathlib import Path

import kif

HERE = Path(__file__).parent
fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}\n     got: {got!r}\n    want: {want!r}")


def one(body, prev_dest=None):
    """指し手1つを USI に変換する（消費時間つきの行として通す）。"""
    _, moves = kif.parse(f"1 {body}   ( 0:01/00:00:01)\n")
    return moves[0]["usi"]


# ── 「同」: 実棋譜を書き換えた差分テスト ───────────────────────────
def dou_differential(path):
    """dest が直前手と同じ手を「同　X(src)」に書き換えても結果が変わらないこと。"""
    text = path.read_text(encoding="utf-8")
    _, moves = kif.parse(text)

    def dest_of(u):
        return u.rstrip("+")[-2:] if u else None

    # 何手目のKIF表記を書き換えるか決める
    rewrite, prev = {}, None
    for mv in moves:
        d = dest_of(mv["usi"])
        if mv["usi"] and d == prev and not mv["kif"].endswith("打"):
            rewrite[mv["n"]] = "同　" + mv["kif"][2:]
        prev = d

    if not rewrite:
        fails.append(f"{path.name}: 「同」に書き換えられる手が1つも無い（テストが素通り）")
        return

    out = []
    for line in text.splitlines():
        m = kif.MOVE_RE.match(line)
        if m and int(m.group(1)) in rewrite:
            out.append(f"{m.group(1)} {rewrite[int(m.group(1))]}   ( 0:01/00:00:01)")
        else:
            out.append(line)

    _, got = kif.parse("\n".join(out))
    check(f"「同」書き換え {path.name}（{len(rewrite)}手: {sorted(rewrite)}）",
          kif.usi_moves(got), kif.usi_moves(moves))


for f in sorted((HERE / "games").glob("*.kif")):
    dou_differential(f)

# 全角スペースが無い「同歩(76)」も同じに読めること
_, m = kif.parse("1 ７六歩(77)\n2 同　歩(83)\n")
_, m2 = kif.parse("1 ７六歩(77)\n2 同歩(83)\n")
check("「同」全角スペース有無", kif.usi_moves(m), kif.usi_moves(m2))
check("「同」の移動先が直前手を引き継ぐ", kif.usi_moves(m), ["7g7f", "8c7f"])

# ── 成り・不成・成駒名 ─────────────────────────────────────────
check("成る",         one("７七歩成(76)"), "7f7g+")
check("不成",         one("７七桂不成(89)"), "8i7g")
check("成銀は駒名",   one("５七成銀(48)"), "4h5g")
check("成桂は駒名",   one("５七成桂(48)"), "4h5g")
check("成香は駒名",   one("５七成香(48)"), "4h5g")
check("と金は駒名",   one("５七と(48)"), "4h5g")
check("馬は駒名",     one("５七馬(48)"), "4h5g")
check("龍は駒名",     one("５七龍(48)"), "4h5g")
check("竜も駒名",     one("５七竜(48)"), "4h5g")
check("成銀が成る手は無い（不成表記の混同なし）", one("５七成銀不成(48)"), "4h5g")

# ── 打つ手 ─────────────────────────────────────────────────────
check("打つ",             one("７六歩打"), "P*7f")
check("打を省いた書式",   one("７六歩"), "P*7f")
check("角打ち",           one("８八角打"), "B*8h")

# ── 右・左・直などの修飾は移動元があるので無視してよい ────────────
check("金右", one("５八金右(69)"), "6i5h")
check("銀上", one("５八銀上(59)"), "5i5h")

# ── 落ちるべきものは落ちる ─────────────────────────────────────
# 移動元の括弧があるときは駒名を見ていない（成るかどうかの判定にしか使わない）
# ので、駒名の誤りは検出できない。verify.py がエンジン側で拾う担当。
for bad in ("１０歩(77)",     # 段が漢数字でない
            "同歩(76)",       # 直前手が無いのに「同」
            "７六玉打",       # 打てない駒
            "７六玉"):        # 移動元も「打」も無い
    try:
        kif.parse(f"1 {bad}\n")
        fails.append(f"KifError が出るべき表記が通った: {bad}")
    except kif.KifError:
        pass

# ── 変化・コメント行 ───────────────────────────────────────────
_, m = kif.parse(
    "*これはコメント\n"
    "1 ７六歩(77)\n2 ３四歩(33)\n"
    "変化：2手\n2 ８四歩(83)\n3 ２六歩(27)\n"
)
check("変化以降を本譜に混ぜない", kif.usi_moves(m), ["7g7f", "3c3d"])

# ── 終局手 ─────────────────────────────────────────────────────
_, m = kif.parse("1 ７六歩(77)\n2 投了   ( 0:03/00:00:23)\n")
check("投了は指し手にしない", kif.usi_moves(m), ["7g7f"])
check("投了を終局手として拾う", m[1]["terminal"], "投了")


if fails:
    print(f"✗ {len(fails)}件 失敗\n")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("✓ すべて通った")
