"""クリップボードのKIFを取り込んで games/ に保存し、そのまま検算までやる。

    将棋ウォーズの棋譜再生画面 → 上部のコピーアイコン
    → Macで  python3 add.py

ファイル名は棋譜の「開始日時」から自動で決まる（例: games/20260830_0023.kif）。

保存の部分は slack_watch.py からも呼ぶので関数に分けてある。
取り込み口（クリップボード / Slack）が増えても、ファイル名の決め方は
ここ一箇所だけにしておく。
"""

import re
import subprocess
import sys
from pathlib import Path

import kif

HERE = Path(__file__).parent
GAMES = HERE / "games"


class DuplicateGame(Exception):
    """同じ開始日時の棋譜がすでに games/ にある。

    same=True なら中身まで同一（＝同じ将棋をもう一度貼った）。
    same=False は、同じ日時で違う内容という異常なケース。
    """

    def __init__(self, path, same):
        super().__init__(f"すでに同じ棋譜があります: {path}")
        self.path, self.same = path, same


def looks_like_kif(text):
    """KIFらしき文字列かどうか。取り込む前のふるい分けに使う。"""
    return "手数----指手" in text or bool(re.search(r"^\s*1\s+\S", text, re.M))


def game_path(header):
    """開始日時から保存先を決める（例: games/20260830_0023.kif）。

    書き込まずに「どこに置かれるか」だけ知りたい場面があるので分けてある。
    """
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", header.get("開始日時", ""))
    name = f"{m.group(1)}{m.group(2)}{m.group(3)}_{m.group(4)}{m.group(5)}" if m else "untitled"
    return GAMES / f"{name}.kif"


def save(text, force=False):
    """KIF本文を games/ に保存して (path, header, moves) を返す。

    KifError（読めない）/ ValueError（指し手なし）/ DuplicateGame を投げる。
    """
    header, moves = kif.parse(text)
    if not moves:
        raise ValueError("指し手が1つも見つかりませんでした")

    GAMES.mkdir(exist_ok=True)
    path = game_path(header)
    if path.exists() and not force:
        raise DuplicateGame(path, path.read_text(encoding="utf-8") == text)

    path.write_text(text, encoding="utf-8")
    return path, header, moves


def summary(header, moves):
    """「▲名前 vs △名前 / 全87手 投了」の1行を作る。Slackにもそのまま出す。"""
    return (f"▲{header.get('先手','?')} ({header.get('先手段級','')})  vs  "
            f"△{header.get('後手','?')} ({header.get('後手段級','')})\n"
            f"  {header.get('開始日時','')}  全{len(moves)}手  "
            f"{moves[-1]['terminal'] or ''}")


def main():
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout

    if not looks_like_kif(text):
        sys.exit(
            "クリップボードにKIFが入っていません。\n"
            "将棋ウォーズの棋譜再生画面で上部のコピーアイコンを押してから、もう一度実行してください。\n"
            f"（今クリップボードにあるもの: {text[:60]!r}）"
        )

    try:
        path, header, moves = save(text, force="--force" in sys.argv)
    except kif.KifError as e:
        sys.exit(f"KIFを読めませんでした: {e}")
    except ValueError as e:
        sys.exit(str(e))
    except DuplicateGame as e:
        sys.exit(f"{e}\n上書きするなら --force を付けてください")

    print(f"保存しました: {path}")
    print(summary(header, moves) + "\n")

    print("検算します...")
    r = subprocess.run([sys.executable, "verify.py", str(path)], cwd=HERE)
    if r.returncode != 0:
        sys.exit("\n検算に失敗しました。この棋譜の解析結果は信用できません。")

    print(f"\n解析するには:\n  python3 analyze.py {path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
