# -*- coding: utf-8 -*-
"""tv_engine — IOS3APP 自有电视剧 API 采集引擎（iOS 专用复刻，与采集器零耦合）。

铁律（用户钦定 2026-09-20）：
- 第三方 CMS 资源站只是原料；采集→过滤→归一后发布到本仓库（自有公网 API）；
- App 端只认自家 API；本仓库永久零成人内容（三道过滤闸，见 audit）。

产出（data/）：
- catalog.json      {updated, categories:[{id,name,count}]}
- {cid}.json        该分类全量条目（年份新→旧）：{id,t,y,pic,from,pu,cat}
  App 端自行分页/搜索（index 内嵌条目自带播放数据，无需二次请求）。
"""
import json, os, re, sys, time, urllib.request, concurrent.futures
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCES = [
    ("liangzi", "量子资源", "https://cj.lziapi.com/api.php/provide/vod"),
    ("tianya",  "天涯资源", "https://tyyszy.com/api.php/provide/vod"),
    ("feifan",  "非凡资源", "https://cj.ffzyapi.com/api.php/provide/vod"),
    ("wolong",  "卧龙资源", "https://collect.wolongzyw.com/api.php/provide/vod"),
]
PAGES_PER_CAT = int(os.environ.get("TV_PAGES", "3"))
OUT = os.path.join(os.path.dirname(__file__), "..", "data")

# ── 过滤闸 1：分类级（与 App 端 DefaultSites 同规）──
BANNED_CAT = ["伦理", "三级", "情色", "成人", "色情", "写真", "福利",
              "体育", "球赛", "足球", "篮球", "赛事", "综艺", "动漫", "动画", "真人秀"]

def is_drama_cat(name: str) -> bool:
    return "剧" in name and not name.endswith("片") and "漫剧" not in name \
        and not any(w in name for w in BANNED_CAT)

# ── 过滤闸 2：条目级（标题/分类命中即剔除）──
BANNED_ITEM = BANNED_CAT + ["erotic", "18+", "R级", "极品", "诱惑", "私密", " scandal"]

def item_ok(title: str, typename: str) -> bool:
    bag = (title + typename).lower()
    return not any(w.lower() in bag for w in BANNED_ITEM)

def sanitize_year(raw) -> str:
    if raw is None: return ""
    m = re.search(r"\d{4}", str(raw))
    if not m: return ""
    y = int(m.group())
    if y < 1900: return ""
    cur = datetime.now().year
    return str(min(y, cur))          # 过滤闸：未来年份压回当前年（治 2027/2030 穿越）

UA = {"User-Agent": "Mozilla/5.0"}
def fetch_json(url: str, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == retries: return None
            time.sleep(1.5)

def parse_play(raw: str):
    """MacCMS vod_play_url: 线路###分隔，集#分隔，名$链接。原样透传给 App（App 端 TVBoxPlayParser 同规）。"""
    return raw or ""

def main():
    os.makedirs(OUT, exist_ok=True)
    # cat_name -> {key: item}（跨源按 标题+年份 去重，先到先得=源优先级）
    cats: dict[str, dict] = {}
    stats = {}

    def work(src):
        key, sname, api = src
        got = {}                       # cat_name -> [item]
        d = fetch_json(f"{api}?ac=list")
        if not d: return got, 0
        drama = [c for c in d.get("class", []) if is_drama_cat(c.get("type_name", ""))]
        raw_cnt = 0
        for c in drama:
            cid, cname = c["type_id"], c["type_name"]
            for pg in range(1, PAGES_PER_CAT + 1):
                det = fetch_json(f"{api}?ac=detail&t={cid}&pg={pg}")
                if not det: break
                lst = det.get("list", []) or []
                if not lst: break
                raw_cnt += len(lst)
                for v in lst:
                    title = (v.get("vod_name") or "").strip()
                    tn = (v.get("type_name") or cname).strip()
                    if not title or not item_ok(title, tn): continue
                    pu = v.get("vod_play_url") or ""
                    if "$" not in pu: continue      # 无播放数据=死条目，源头剔除
                    y = sanitize_year(v.get("vod_year"))
                    item = {"id": f"{key}:{v.get('vod_id')}", "t": title, "y": y,
                            "pic": v.get("vod_pic") or "", "from": v.get("vod_play_from") or "",
                            "pu": pu, "cat": tn, "src": sname}
                    got.setdefault(tn, {})
                    k = (title, y)
                    if k not in got[tn]: got[tn][k] = item
        return got, raw_cnt

    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        for (got, raw_cnt), src in zip(ex.map(work, SOURCES), SOURCES):
            stats[src[0]] = raw_cnt
            for tn, items in got.items():
                cats.setdefault(tn, {}).update(items)

    # 分类排序：国产剧优先，其余按体量降序
    def cat_rank(name): return 0 if "国产" in name else 1
    cat_list = sorted(cats.items(), key=lambda kv: (cat_rank(kv[0]), -len(kv[1])))
    catalog = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "engine": "tv_engine v1", "sources": [s[1] for s in SOURCES],
               "categories": []}
    for i, (tn, items) in enumerate(cat_list, 1):
        cid = f"c{i}"
        arr = sorted(items.values(), key=lambda x: x.get("y", ""), reverse=True)
        catalog["categories"].append({"id": cid, "name": tn, "count": len(arr)})
        with open(os.path.join(OUT, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump({"cat": tn, "items": arr}, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, separators=(",", ":"))

    total = sum(c["count"] for c in catalog["categories"])
    print("源采集原始条数:", stats)
    print(f"归一后 {len(cat_list)} 分类 / {total} 部")
    for c in catalog["categories"]:
        print(f"  {c['id']} {c['name']}: {c['count']}")
    audit(total)

def audit(total: int):
    """过滤闸 3：发布前机检——扫描全部产出物，命中违禁词即中止（违禁内容绝不出仓）。"""
    banned = [w.lower() for w in BANNED_ITEM]
    hits = []
    for fn in os.listdir(OUT):
        if not fn.endswith(".json"): continue
        with open(os.path.join(OUT, fn), encoding="utf-8") as f:
            text = f.read().lower()
        for w in banned:
            if w in text: hits.append((fn, w))
    if hits:
        raise SystemExit(f"AUDIT FAILED 成人/违禁词泄漏: {hits[:10]} —— 禁止发布！")
    print(f"AUDIT PASS ✓ 全部产出零违禁词（{total} 部可安全发布）")

if __name__ == "__main__":
    main()
