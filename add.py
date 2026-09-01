"""クリップボードのKIFを取り込んで games/ に保存し、そのまま検算までやる。

    将棋ウォーズの棋譜再生画面 → 上部のコピーアイコン
    → Macで  python3 add.py

ファイル名は棋譜の「開始日時」から自動で決まる（例: games/20260830_0023.kif）。
"""

import re
import subprocess
import sys
from pathlib import Path

import kif

GAMES = Path(__file__).parent / "games"


def main():
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout

    if "手数----指手" not in text and not re.search(r"^\s*1\s+\S", text, re.M):
        sys.exit(
            "クリップボードにKIFが入っていません。\n"
            "将棋ウォーズの棋譜再生画面で上部のコピーアイコンを押してから、もう一度実行してください。\n"
            f"（今クリップボードにあるもの: {text[:60]!r}）"
        )

    try:
        header, moves = kif.parse(text)
    except kif.KifError as e:
        sys.exit(f"KIFを読めませんでした: {e}")

    if not moves:
        sys.exit("指し手が1つも見つかりませんでした")

    # 開始日時からファイル名を作る
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", header.get("開始日時", ""))
    name = f"{m.group(1)}{m.group(2)}{m.group(3)}_{m.group(4)}{m.group(5)}" if m else "untitled"

    GAMES.mkdir(exist_ok=True)
    path = GAMES / f"{name}.kif"
    if path.exists() and "--force" not in sys.argv:
        sys.exit(f"すでに同じ棋譜があります: {path}\n上書きするなら --force を付けてください")

    path.write_text(text, encoding="utf-8")

    sente, gote = header.get("先手", "?"), header.get("後手", "?")
    print(f"保存しました: {path}")
    print(f"  ▲{sente} ({header.get('先手段級','')})  vs  △{gote} ({header.get('後手段級','')})")
    print(f"  {header.get('開始日時','')}  全{len(moves)}手  "
          f"{moves[-1]['terminal'] or ''}\n")

    print("検算します...")
    r = subprocess.run([sys.executable, "verify.py", str(path)],
                       cwd=Path(__file__).parent)
    if r.returncode != 0:
        sys.exit("\n検算に失敗しました。この棋譜の解析結果は信用できません。")

    rel = path.relative_to(Path(__file__).parent)
    print(f"\n解析するには:\n  python3 analyze.py {rel}")


if __name__ == "__main__":
    main()
