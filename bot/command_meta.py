"""后端命令的中文显示名与说明。

控制台「指令菜单」显示中文，但真正的命令 key（例如 `riven_market`）保持不变，
避免前端展示影响解析。术语按 Warframe 中文 Wiki / 游戏内叫法：
Arbitration=仲裁、Archon Hunt=执刑官猎杀、Void Fissure=虚空裂缝、
Void Storm=虚空风暴、Deep Archimedea=深层科研、Temporal Archimedea=时光科研。
"""

from __future__ import annotations

from typing import Dict, Tuple


COMMAND_META: Dict[str, Tuple[str, str]] = {
    "alert": ("降价提醒", "监控 warframe.market 价格，达到目标价时提醒"),
    "alerts": ("提醒列表", "查看、删除本会话的降价提醒"),
    "arbi_schedule": ("仲裁排期", "未来 7 天仲裁节点轮换"),
    "arbi_tiers": ("仲裁评级", "仲裁节点梯队与本地推算"),
    "arbitration": ("当前仲裁", "当前仲裁节点、任务与敌人"),
    "archon": ("执刑官猎杀", "本周执刑官猎杀任务"),
    "baro": ("虚空商人", "Baro Ki'Teer 位置、商品与剩余时间"),
    "bounties": ("赏金任务", "各地可接赏金一览"),
    "bounty_cavia": ("圣所赏金", "解剖圣所（Cavia）赏金"),
    "bounty_hex": ("1999 赏金", "霍瓦尼亚（The Hex）赏金"),
    "bounty_zariman": ("扎里曼赏金", "扎里曼（The Holdfasts）赏金"),
    "cetus": ("希图斯", "希图斯时间、赏金与帐篷"),
    "circuit": ("无尽回廊", "本周回廊灵化与轮换"),
    "cycles": ("平原轮换", "地球、金星、火卫二时间"),
    "data_status": ("数据状态", "各数据源更新时间与缓存状态"),
    "deals": ("每日特惠", "Darvo 每日特惠"),
    "deep_research": ("深层科研", "深层科研（Deep Archimedea）"),
    "effective_arbitration": ("高效仲裁", "未来 24 小时高价值仲裁"),
    "events": ("游戏活动", "当前游戏内活动"),
    "fissures": ("虚空裂缝", "普通虚空裂缝"),
    "frame_rank": ("战甲价格榜", "Prime 战甲价格排行"),
    "help": ("帮助菜单", "显示命令菜单"),
    "junk_bronze": ("铜垃圾", "铜级 Prime 垃圾地板价"),
    "junk_fake_gold": ("假金垃圾", "非 Prime 金卡垃圾价"),
    "junk_fake_silver": ("假银垃圾", "非 Prime 银卡垃圾价"),
    "junk_gold": ("金垃圾", "金级 Prime 垃圾地板价"),
    "junk_overview": ("垃圾行情", "金 / 银 / 铜垃圾总览"),
    "junk_silver": ("银垃圾", "银级 Prime 垃圾地板价"),
    "mod": ("模组查询", "Mod 搜索与价格"),
    "news": ("官方新闻", "Warframe 官方新闻"),
    "nightwave": ("午夜电波", "午夜电波挑战与进度"),
    "price": ("价格查询", "只看价格、买卖单与行情"),
    "query": ("物品查询", "物品详情、实时报价与走势"),
    "relic": ("遗物查询", "遗物内容、杜卡德、出入库与 WM 行情"),
    "research": ("科研总览", "深层科研与时光科研总览"),
    "riven": ("紫卡范围", "武器紫卡词条范围与倾向"),
    "riven_group": ("紫卡分析", "按主武器 / 副武器 / 近战等分组看范围"),
    "riven_market": ("紫卡市场", "在线/游戏中卖家的紫卡拍卖"),
    "riven_rank": ("紫卡排行", "零洗紫卡价格榜"),
    "rotation": ("轮换商店", "言录使、格拉斯、埃莉诺轮换"),
    "rotation_acrithis": ("言录使轮换", "本周言录使商品"),
    "rotation_eleanor": ("埃莉诺轮换", "埃莉诺信条近战加成"),
    "rotation_glast": ("格拉斯轮换", "格拉斯终幕武器加成"),
    "set": ("套装拆件", "套装与部件比价"),
    "sortie": ("突击任务", "当前突击任务"),
    "steel_fissures": ("钢铁裂缝", "钢铁之路虚空裂缝"),
    "steel_path": ("钢铁侵袭", "钢铁之路侵袭与奖励"),
    "storms": ("虚空风暴", "航道星舰虚空风暴"),
    "subs": ("订阅列表", "查看本会话事件订阅"),
    "subscribe": ("事件订阅", "新裂缝/警报/入侵等出现时推送"),
    "temporal_research": ("时光科研", "时光科研（Temporal Archimedea）"),
    "today_arbitration": ("今日仲裁", "未来 24 小时仲裁轮换"),
    "translate": ("中英翻译", "物品名中英对照"),
    "unalert": ("取消提醒", "删除降价提醒"),
    "unsubscribe": ("取消订阅", "删除事件订阅"),
    "update": ("更新数据", "立即刷新缓存数据"),
    "warframe": ("战甲搜索", "按战甲分类搜索"),
    "weapon": ("武器搜索", "按武器分类搜索"),
    "wiki": ("英文维基", "查询 Warframe Wiki"),
    "world_alerts": ("世界警报", "当前警报与双方奖励"),
    "world_invasions": ("入侵任务", "入侵进度与双方 Battle Pay"),
    "world_summary": ("世界状态", "完整世界状态总览"),
}
