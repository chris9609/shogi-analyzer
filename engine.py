"""USIエンジンを叩く薄いドライバ。

パイプに `go` と `quit` を一度に流し込むと探索が即打ち切られるので、
必ずプロセスを開いたまま bestmove まで読む。
"""

import subprocess

MATE_SCORE = 100000  # 詰みを表す便宜上の点数


class Engine:
    def __init__(self, path="fairy-stockfish", threads=4, hash_mb=512, variant="shogi"):
        self.p = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._send("usi")
        self._read_until("usiok")
        self._send(f"setoption name UCI_Variant value {variant}")
        self._send(f"setoption name Threads value {threads}")
        self._send(f"setoption name Hash value {hash_mb}")
        self.ready()

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

    def ready(self):
        self._send("isready")
        self._read_until("readyok")

    def analyse(self, moves, movetime=1000):
        """startpos から moves を指した局面を探索する。

        戻り値の score は「手番側から見た」点数（USI の仕様どおり）。
        詰みは MATE_SCORE を手数で目減りさせた値に変換する。
        """
        self._send("position startpos" + (" moves " + " ".join(moves) if moves else ""))
        self._send(f"go movetime {movetime}")
        lines = self._read_until("bestmove")

        score, pv, depth = None, [], 0
        longest_pv = []  # 最終反復のPVが短く切れたときの保険
        for line in lines:
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
                if len(pv) > len(longest_pv):
                    longest_pv = pv
                if "depth" in tok:
                    depth = int(tok[tok.index("depth") + 1])
            except (ValueError, IndexError):
                continue

        # 置換表のカットで最終反復のPVが1〜2手に切れることがある。
        # 読み筋は見せるためのものなので、その場合は探索中で一番長かったものを使う
        if len(pv) < 4 and len(longest_pv) > len(pv):
            pv = longest_pv

        best = None
        for line in lines:
            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) > 1 and parts[1] not in ("resign", "(none)"):
                    best = parts[1]

        return {"score": score, "pv": pv, "best": best, "depth": depth}

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
