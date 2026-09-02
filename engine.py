"""USIエンジンを叩く薄いドライバ。

パイプに `go` と `quit` を一度に流し込むと探索が即打ち切られるので、
必ずプロセスを開いたまま bestmove まで読む。
"""

import subprocess
from pathlib import Path

MATE_SCORE = 100000  # 詰みを表す便宜上の点数

# NNUE評価関数の置き場。ここに *.nnue を置いておけば自動で積む。
# （152MBあるのでリポジトリには入れない。README に取得先を書いてある）
NNUE_DIR = Path(__file__).parent / "nnue"


def find_nnue():
    """nnue/ にある評価関数のパス。無ければ None。"""
    files = sorted(NNUE_DIR.glob("*.nnue")) if NNUE_DIR.is_dir() else []
    return str(files[0]) if files else None


class Engine:
    def __init__(self, path="fairy-stockfish", threads=4, hash_mb=512,
                 variant="shogi", eval_file=None):
        # eval_file: None なら nnue/ を自動で探す。"" なら明示的に classical
        if eval_file is None:
            eval_file = find_nnue()
        self.path = path
        self.name = path
        self.eval_file = eval_file or None
        # "classical" か "NNUE"。エンジン自身の申告で決める（ファイルの有無では決めない）
        self.eval_mode = "unknown"
        self.strings = []  # エンジンが吐いた info string を溜めておく
        self._multipv = 1

        self.p = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._send("usi")
        for line in self._read_until("usiok"):
            if line.startswith("id name "):
                self.name = line[len("id name "):].strip()
            self._note(line)
        self._send(f"setoption name UCI_Variant value {variant}")
        self._send(f"setoption name Threads value {threads}")
        self._send(f"setoption name Hash value {hash_mb}")
        if self.eval_file:
            # NNUE評価関数を積む。積めたかどうかはファイルの有無では決めず、
            # _probe_eval() でエンジンに名乗らせる（eval_mode）
            self._send("setoption name Use_NNUE value true")
            self._send(f"setoption name EvalFile value {self.eval_file}")
        else:
            self._send("setoption name Use_NNUE value false")
        self.ready()
        self._probe_eval()

    # ── 低レベル ───────────────────────────────────────────────
    def _send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def _read_until(self, token):
        lines = []
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("エンジンが応答を返さずに終了した")
            line = line.rstrip()
            lines.append(line)
            if line.startswith(token):
                return lines

    def _note(self, line):
        """`info string ...` を拾って評価モードを判定する。

        fairy-stockfish は評価関数を積めなかったとき、エラーではなく
        `info string classical evaluation enabled` と言って動き続ける。
        評価値を信用してよいかはこの申告でしか分からない。
        """
        if not line.startswith("info string"):
            return
        self.strings.append(line)
        low = line.lower()
        if "nnue evaluation" in low and "enabled" in low:
            self.eval_mode = "NNUE"
        elif "classical evaluation" in low:
            self.eval_mode = "classical"

    def ready(self):
        self._send("isready")
        for line in self._read_until("readyok"):
            self._note(line)

    def _probe_eval(self):
        """評価モードを確定させるためだけの、ごく短い探索。

        fairy-stockfish は `classical evaluation enabled` を usi でも isready
        でもなく、最初の go のときに吐く。解析を始める前に評価モードを
        名乗らせておかないと、ヘッダに何を書けばよいか分からない。
        """
        self._send("position startpos")
        self._send("go movetime 1")
        for line in self._read_until("bestmove"):
            self._note(line)

    def describe(self):
        """人が読む用の1行。評価値の意味が変わるので、出力に必ず添えること。"""
        s = f"{self.name} / {self.eval_mode}"
        if self.eval_file:
            s += f" ({Path(self.eval_file).name})"
        return s

    # ── 探索 ───────────────────────────────────────────────────
    def _set_multipv(self, n):
        if n != self._multipv:
            self._send(f"setoption name MultiPV value {n}")
            self._multipv = n
            self.ready()

    def analyse(self, moves, movetime=1000, multipv=1):
        """startpos から moves を指した局面を探索する。

        戻り値の score は「手番側から見た」点数（USI の仕様どおり）。
        詰みは MATE_SCORE を手数で目減りさせた値に変換する。
        candidates は根本手の候補（multipv 1 が先頭）。
        """
        self._set_multipv(multipv)
        self._send("position startpos" + (" moves " + " ".join(moves) if moves else ""))
        self._send(f"go movetime {movetime}")
        lines = self._read_until("bestmove")

        # 🔑 MultiPV>1 では multipv 1/2/3 の行が繰り返し流れてくる。
        # 「最後にスコアが載っていた行」を採ると、局面の評価値が
        # 3番目の候補手の評価値（＝いちばん悪い手）にすり替わる。
        # さらに、順位ごとに最後の行を採るのも駄目で、最終反復が途中で
        # 打ち切られると順位2と3が別々の反復から来て、同じ手が2回並ぶ
        # （実測で局面の約2割で起きた）。
        # 順位が出そろった反復のうち、いちばん深いものを丸ごと使う。
        # さらに同じ反復の中でも、最後に出し直されたブロックだけを採る。
        by_depth = {}  # depth -> {rank: (score, pv)}
        longest = {}   # rank -> pv   読み筋が切れたときの保険
        for line in lines:
            self._note(line)
            if not line.startswith("info ") or " score " not in line:
                continue
            # 探索途中の暫定値。読み筋が1手だけの短い行が混ざるので採用しない
            if "lowerbound" in line or "upperbound" in line:
                continue
            tok = line.split()
            if "pv" not in tok:
                continue
            try:
                i = tok.index("score")
                kind, val = tok[i + 1], int(tok[i + 2])
                score = val if kind == "cp" else \
                    (MATE_SCORE - abs(val)) * (1 if val > 0 else -1)
                pv = tok[tok.index("pv") + 1:]
                rank = int(tok[tok.index("multipv") + 1]) if "multipv" in tok else 1
                depth = int(tok[tok.index("depth") + 1]) if "depth" in tok else 0
            except (ValueError, IndexError):
                continue
            by_depth.setdefault(depth, {})[rank] = (score, pv)
            if len(pv) > len(longest.get(rank, [])):
                longest[rank] = pv

        # 🔑 局面の評価値・最善手・候補手は、すべて「同じ反復」から採る。
        # multipv の行は反復ごと・順位ごとにバラバラに出るので、深さをまたいで
        # 拾うと、比べられない数字が1つのリストに並ぶ（2位のほうが1位より
        # 高い評価値、といった見え方になる）。
        # 順位が出そろった反復のうち、いちばん深いものを丸ごと使う。
        depth = max(by_depth, key=lambda d: (len(by_depth[d]), d), default=0)
        found = by_depth.get(depth, {})

        def pv_of(rank):
            # 置換表のカットで最終反復のPVが1〜2手に切れることがある。
            # 読み筋は見せるためのものなので、その場合は探索中に見えた
            # 一番長いものを使う。ただし初手が違うものは別の手なので使わない
            pv, alt = found[rank][1], longest.get(rank, [])
            return alt if len(pv) < 4 and len(alt) > len(pv) and alt[:1] == pv[:1] else pv

        # 同じ反復の中でも、multipv の行は一斉に出るわけではない。エンジンは
        # 根本手を1つ探索するたびに並べ替えて1行ずつ出すので、先に出た
        # `multipv 1` が並べ替え前の順位を映していて、`multipv 2` に同じ手が
        # 出ることがある。番号は当てにせず、手で重複を潰して評価値順に並べ直す
        best_by_move = {}
        for r in sorted(found):
            mv = (pv_of(r) or [None])[0]
            if mv is None:
                continue
            if mv not in best_by_move or found[r][0] > best_by_move[mv][0]:
                best_by_move[mv] = (found[r][0], pv_of(r))
        candidates = [
            {"rank": i + 1, "cp": sc, "move": mv, "pv": line}
            for i, (mv, (sc, line)) in enumerate(
                sorted(best_by_move.items(), key=lambda kv: -kv[1][0]))
        ]

        score = candidates[0]["cp"] if candidates else None
        pv = candidates[0]["pv"] if candidates else []

        # 最善手も同じ反復から採る。エンジンの `bestmove` 行は最後の
        # （順位が出そろっていない）反復まで見ているので、そのまま使うと
        # 「最善手」と「候補1位」が食い違って見える。候補が取れなかった
        # ときだけ bestmove 行に頼る
        best = candidates[0]["move"] if candidates else None
        if best is None:
            for line in lines:
                if line.startswith("bestmove"):
                    parts = line.split()
                    if len(parts) > 1 and parts[1] not in ("resign", "(none)"):
                        best = parts[1]

        return {"score": score, "pv": pv, "best": best, "depth": depth,
                "candidates": candidates}

    def close(self):
        try:
            self._send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
