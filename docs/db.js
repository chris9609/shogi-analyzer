// 公開ページの設定と、Supabase（PostgREST）を読むための小さな関数。
// publishable key はブラウザに配る前提の公開鍵。書き込みは RLS で塞いであり、
// これで出来るのは select だけ（schema.sql）。secret key は絶対にここに書かない。

export const SUPABASE_URL = "https://urkfxpdbavfvwthzjfgs.supabase.co";
export const SUPABASE_KEY = "sb_publishable_uBfyx2CEbPVJbPNwbQDQLQ_p47PUVE_";
export const ME = "christel09";

export async function rest(path) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
  });
  if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// PostgREST は既定で最大1000行しか返さない。超えた分が黙って落ちると
// 一覧の悪手の数が古い局から欠けていくので、全部読み切るまでページを送る
export async function restAll(path, page = 1000) {
  const out = [];
  for (let from = 0; ; from += page) {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`,
                 Range: `${from}-${from + page - 1}` },
    });
    if (!r.ok) throw new Error(`Supabase ${r.status}: ${(await r.text()).slice(0, 200)}`);
    const rows = await r.json();
    out.push(...rows);
    if (rows.length < page) return out;
  }
}

// 1局ぶんの解析JSON（export.py が吐いたもの）。ビューアはこれをそのまま使う
export async function loadGame(id) {
  if (!id || !/^\d{8}_\d{4}$/.test(id)) throw new Error("URL に ?id=20260918_1130 の形で対局を指定してください");
  const rows = await rest(`games?id=eq.${id}&select=data`);
  if (!rows.length) throw new Error(`対局 ${id} は Supabase にありません（sync.py で送っていますか）`);
  return rows[0].data;
}
