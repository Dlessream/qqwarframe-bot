import json
from collections import Counter

data = json.load(open("/app/data/board_cache.json"))
riven = data.get("riven") or {}
print("版本:", data.get("version"), "紫卡条目:", len(riven))
sizes = Counter()
legacy = 0
rows = []
sample = None
for slug, raw in riven.items():
    entry = raw if isinstance(raw, list) else [raw.get("t"), raw.get("c")]
    cands = entry[1] if len(entry) > 1 else []
    sizes[len(cands or [])] += 1
    if len(entry) > 2 and entry[2]:
        legacy += 1
    if sample is None:
        sample = (slug, entry)
    for cand in (cands or []):
        rows.append((int(cand[0]), slug, cand[2], cand[1]))
print("候选条数分布:", dict(sorted(sizes.items())))
print("legacy 标记:", legacy)
print("样例:", sample[0], json.dumps(sample[1], ensure_ascii=False)[:200])
rows.sort(reverse=True)
price_w = Counter(r[0] for r in rows if r[0] >= 3000)
print("同一价格刷在多把武器上:", price_w.most_common(6))
print("最高 10 条:")
for price, slug, seller, rank in rows[:10]:
    print("  %6dp %-22s %-14s R%s" % (price, slug, seller, rank))
