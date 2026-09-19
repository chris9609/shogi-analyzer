"""解析JSONをテンプレートに埋め込んで、単体で開けるHTMLを書き出す。

    python3 build.py out/20260901_1056.json
    python3 build.py out/20260901_1056.json -o out/viewer.html
    python3 build.py --web -o docs/game.html      # JSONを埋め込まず、Supabase から読む版
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).parent
PLACEHOLDER = "/*__DATA__*/{}"

# --web のとき、埋め込みJSONの代わりに差し込むもの。
# top-level await を使うので <script> は type="module" に変える
WEB_LOADER = """await loadGame(new URLSearchParams(location.search).get("id")).catch(err => {
  document.querySelector(".wrap").innerHTML =
    `<p style="padding:40px 16px;color:var(--ink2)">${err.message}<br><a href="./">← 全対局</a></p>`;
  throw err;
});
document.title = `▲${DATA.sente} vs △${DATA.gote}`;"""
BACK_LINK = '<a class="back" href="./">← 全対局</a>\n'
BACK_CSS = ('.back{font-size:12px;color:var(--muted);text-decoration:none;display:inline-block;'
            'margin-bottom:6px}.back:hover{color:var(--ink)}\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonfile", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--web", action="store_true",
                    help="JSONを埋め込まず、開いたときに docs/db.js 経由で Supabase から読む"
                         "（?id=20260918_1130 で対局を指定）")
    ap.add_argument("--bare", action="store_true",
                    help="html/head/bodyの外枠を付けない（Artifactとして公開する用）")
    args = ap.parse_args()

    tpl = (HERE / "viewer_template.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise SystemExit("テンプレートに差し込み位置が見つかりません")

    if args.web:
        if args.jsonfile:
            raise SystemExit("--web のときはJSONを指定しません（開いたときに読みます）")
        body = (tpl.replace(PLACEHOLDER, WEB_LOADER)
                   .replace("<script>", '<script type="module">\nimport { loadGame } from "./db.js";', 1)
                   .replace('<div class="players">', BACK_LINK + '<div class="players">', 1)
                   .replace("</style>", BACK_CSS + "</style>", 1)
                   # ?ply=N で途中の局面から開ける（一覧の「痛かった手」から飛ぶ用）
                   .replace("go(0, {silent: true});",
                            'go(+(new URLSearchParams(location.search).get("ply")) || 0, {silent: true});', 1))
    else:
        if not args.jsonfile:
            raise SystemExit("JSONファイルを指定してください（Supabase から読む版は --web）")
        data = json.loads(Path(args.jsonfile).read_text(encoding="utf-8"))
        # </script> がデータ中に現れるとHTMLが壊れるので潰しておく
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

        body = tpl.replace(PLACEHOLDER, payload)

        # タイトルに対局者を入れる。複数局を並べたときに、タブでもギャラリーでも
        # どの将棋か見分けられるようにする（テンプレートのままだと全部同じ名前になる）
        title = f"▲{data.get('sente','?')} vs △{data.get('gote','?')}"
        body = body.replace("<title>棋譜検討盤</title>", f"<title>{title}</title>", 1)

    # ローカルで開けるよう、完全なHTML文書として包む。
    # （Artifactとして公開する場合はこの外枠が向こうで付くので viewer_template.html を渡す）
    doc = body if args.bare else ('<!doctype html>\n<html lang="ja">\n<head>\n'
           '<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
           '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
           '</head>\n<body>\n' + body + '\n</body>\n</html>\n')

    out = Path(args.out) if args.out else \
        (HERE / "docs" / "game.html" if args.web else Path(args.jsonfile).with_suffix(".html"))
    out.parent.mkdir(exist_ok=True, parents=True)
    out.write_text(doc, encoding="utf-8")
    print(f"書き出し: {out}  ({out.stat().st_size // 1024}KB)")
    print(f"開く: open {out}")


if __name__ == "__main__":
    main()
