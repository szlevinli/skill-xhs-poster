---
name: xiaohongshu-product-poster
description: |
  小红书商家后台自动化工具：从商品管理挑选商品并自动发布笔记。
  
  使用场景：
  1. 每天自动获取最新10个商品
  2. 为每个商品生成5篇不同角度的笔记内容
  3. 批量发布50篇笔记（10商品×5篇）
  
  工作模式：三阶段独立执行（准备→内容→发布）
  依赖技能：Agent Browser、xiaohongshu-mcp
---

# 小红书商品笔记自动发布

## 心智模型：三阶段工作流

本技能采用**三阶段独立执行**的设计，各阶段通过文件传递数据，支持独立调度和断点续传。

```
┌─────────────────────────────────────────────────────────────────┐
│                         一天的工作流                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  阶段1: 准备          阶段2: 内容           阶段3: 发布           │
│  ━━━━━━━━━━          ━━━━━━━━━━          ━━━━━━━━━━             │
│                                                                  │
│  ┌─────────┐         ┌─────────┐         ┌─────────┐            │
│  │获取10个 │   ───>  │搜索热门 │   ───>  │批量发布 │            │
│  │商品     │         │笔记参考 │         │50篇笔记 │            │
│  │         │         │         │         │         │            │
│  │下载30张 │         │LLM生成  │         │支持断点 │            │
│  │主图     │         │50篇内容 │         │续传     │            │
│  └────┬────┘         └────┬────┘         └────┬────┘            │
│       │                   │                   │                 │
│       ▼                   ▼                   ▼                 │
│  today-pool.json     contents.json      publish-log.json        │
│  (商品+图片)         (标题+正文+标签)    (发布记录)              │
│                                                                  │
│  Cron: 8:00          Cron: 10:00        Cron: 20:00             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 阶段说明

| 阶段 | 职责 | 输入 | 输出 | 执行时机 |
|------|------|------|------|----------|
| **阶段1** | 准备商品和图片 | 小红书商品列表 | `today-pool.json` + 30张图片 | 每天早上 |
| **阶段2** | 生成笔记内容 | `today-pool.json` + 热门笔记分析 | `contents.json` (50篇内容) | 每天上午 |
| **阶段3** | 发布笔记 | `today-pool.json` + `contents.json` | 发布到小红书 + 记录日志 | 每天晚上 |

### 阶段依赖

- **阶段2** 依赖阶段1：如果 `today-pool.json` 不存在，自动执行阶段1
- **阶段3** 依赖阶段1和2：如果前置阶段未完成，自动按顺序执行

### 数据流转

```
阶段1输出: today-pool.json
{
  "date": "2026-03-03",
  "products": [...],     // 10个商品信息
  "images": {...}        // 每个商品的3张图片路径
}

   │
   │ 阶段2读取商品信息，为每个商品生成5篇内容
   ▼

阶段2输出: contents.json
{
  "date": "2026-03-03",
  "search_results": {...},  // Tavily搜索结果
  "contents": {
    "商品ID-1": [
      {angle: 1, title, content, tags},
      {angle: 2, title, content, tags},
      ...
    ],
    ...
  }
}

   │
   │ 阶段3读取内容和图片，批量发布
   ▼

阶段3输出: 发布到小红书 + publish-log.json
```

---

## 功能概述

本技能实现从**小红书商家后台**自动挑选商品并发布笔记的完整流程：

1. 🔐 自动登录商家后台（支持短信验证）
2. 📦 **获取最新10个商品，按规则轮流选择**
3. 🖼️ **进入商品详情页，下载前3张主图**
4. 📝 **生成5篇不同角度的笔记内容**（每个商品每天5篇）
5. 📤 发布笔记并关联商品
6. 📝 记录已发布历史，实现"10个商品轮流，每个每天5篇"
7. ⚠️ **检查每日50篇配额限制**

---

## 业务规则

### 发帖策略（重要）

```
📅 当天锁定策略：
  ├─ 每天开始时获取当前最新10个商品
  ├─ 这10个商品作为"今日商品池"，当天固定不变
  ├─ 即使当天有新商品上架，今日不处理（明天自然进入列表）
  └─ 确保当天每个商品都能发满5篇

🔄 轮流发规则：
  ├─ 10个商品轮流发（1→2→3→...→10→1→2...）
  ├─ 每个商品每天最多发 5 篇笔记
  ├─ 每天总上限 50 篇（小红书限制）
  └─ 每个笔记只关联 1 个商品

📋 示例时间线：
  Day 1 08:00: 获取商品池 [A,B,C,D,E,F,G,H,I,J]
  Day 1 08:30: 发A(第1篇) → B(第1篇) → C(第1篇)... 轮流
  Day 1 10:00: 新商品K上架（今日不处理，明天进入池）
  Day 1 12:00: 中断，当前发到 F(第2篇)
  Day 1 14:00: 恢复，从 F(第3篇) 继续轮流
  Day 1 结束: A-J 各发5篇 = 50篇 ✓
  
  Day 2 08:00: 获取新商品池 [K,B,C,D,E,F,G,H,I,J]（A被挤出）
  Day 2: 重新开始轮流...
```

### 中断恢复规则

```
场景：发了23篇后中断
├─ 商品A: 3篇（已发）
├─ 商品B: 3篇（已发）
├─ 商品C: 2篇（已发）
├─ 商品D: 1篇（已发）
└─ 商品E-J: 0篇

恢复时：继续从商品D的第2篇开始，保持轮流顺序
不跳过、不重发已完成的商品
```

### 笔记内容策略（每个商品5篇不同角度）

内容编写采用**"学习爆款+结合商品"**的双轮驱动模式：

```
┌─────────────────────────────────────────────────────────────┐
│                    内容生成流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  热门笔记分析 (阶段2步骤1-2)                                  │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━                                │
│  ├─ 搜索"发夹"获取20-30篇热门笔记                            │
│  ├─ 分析高互动笔记（点赞>1000）                               │
│  ├─ 提取爆款标题公式（如："xx感绝了"、"被问爆的xx"）          │
│  ├─ 总结文案结构（痛点→产品→场景→情感）                       │
│  └─ 统计热门标签组合和emoji使用规律                           │
│                          │                                   │
│                          ▼                                   │
│  结合商品编写 (阶段2步骤3)                                    │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━                                  │
│  ├─ 读取商品主图，提取视觉特征（颜色/图案/材质/款式）          │
│  ├─ 为每个商品编写5篇，分别侧重：                              │
│  │   1️⃣ 颜值角度 → 颜色/图案/外观设计                         │
│  │   2️⃣ 材质角度 → 触感/质感/工艺细节                         │
│  │   3️⃣ 搭配角度 → 使用场景/发型/穿搭建议                      │
│  │   4️⃣ 风格角度 → 氛围感/情感联想/个性化表达                   │
│  │   5️⃣ 体验角度 → 使用感受/解决问题/惊喜发现                   │
│  └─ 运用爆款公式，确保每篇风格统一但角度独特                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

| 篇数 | 内容角度 | 编写思路 | 参考爆款元素 |
|------|----------|----------|--------------|
| 1 | 颜色颜值 | 描述主图色彩/图案，营造视觉吸引力 | 标题emoji + "xx感"形容词 |
| 2 | 材质质感 | 从图片质感推测材质，强调品质感 | "真的不一样"、"绝了" |
| 3 | 搭配场景 | 结合款式给出发型/穿搭建议 | "手残党必入"、"一分钟搞定" |
| 4 | 风格情感 | 赋予商品情感价值，引发共鸣 | 氛围描述 + 情感联想 |
| 5 | 使用体验 | 模拟真实使用感受，增加可信度 | "终于找到"、"感动"、

### ⚠️ 内容生成约束（重要）

**禁止内容**：
- ❌ **不提及价格**（如"便宜"、"贵"、"性价比"、"值"等）
- ❌ **不胡乱猜测**（如"一套4个"、"送闺蜜"等未确认的信息）
- ❌ **不做虚假宣传**（如"明星同款"、"网红推荐"等无法验证的说法）

**必须遵循**：
- ✅ **基于图片事实**：只描述图片中确认的特征（颜色、图案、材质）
- ✅ **基于商品名称**：从名称中提取款式、风格等信息
- ✅ **个人体验角度**：用"我觉得"、"我发现"等第一人称表达
- ✅ **通用场景描述**：日常、通勤、约会等普适场景

**内容生成检查清单**：
```
□ 是否提及价格？→ 删除
□ 是否包含未确认信息？→ 删除或修改
□ 是否基于图片特征？→ 确保描述与图片一致
□ 是否使用第一人称体验？→ 优先使用
```

---

## 前置条件

### 依赖技能
- **Agent Browser** - 浏览器自动化操作（商家后台）
- **xiaohongshu-mcp** - 小红书热门笔记获取和内容分析

### 环境要求
- Node.js + agent-browser CLI
- Python 3.8+
- xiaohongshu-mcp server 运行中（端口18060）

### 账号信息
- 商家后台账号：`13923795110`
- 商家后台地址：`https://ark.xiaohongshu.com`

### 文件结构
```
~/.openclaw/workspace/
├── xiaohongshu-auth.json          # 登录状态文件
├── product-images/                # 商品图片缓存
│   └── {商品ID}/
│       ├── 1.jpg
│       ├── 2.jpg
│       └── 3.jpg
├── xiaohongshu-products.json      # 商品历史记录
└── xiaohongshu-publish-log.json   # 发布日志（按天统计）
```

---

## 核心工作流程

```
初始化阶段（每天一次）：
  ├─ 获取当前最新10个商品
  ├─ 保存为"今日商品池"（当天固定不变）
  └─ 检查今日是否已有发布记录（中断恢复）

Step 1: 检查配额
  ├─ 读取今日已发数量
  └─ 如已达50篇，停止并提示

Step 2: 恢复登录状态
  ├─ 停止浏览器
  ├─ 加载state
  └─ 打开商品管理

Step 3: 确定当前发布位置
  ├─ 如有今日发布记录 → 找到最后发布的商品和篇数
  ├─ 计算下一个要发的商品（按轮流顺序）
  └─ 如该商品图片已下载 → 复用；否则进入Step 4下载

Step 4: 进入商品详情页下载图片（仅首次需要）
  ├─ 点击商品名称进入详情
  ├─ 提取商品ID
  ├─ 下载前3张主图（保存到 product-images/{商品ID}/）
  └─ 返回商品管理列表

Step 5: 点击"去发布"
  ├─ 找到对应商品行
  └─ 点击"去发布"

Step 6: 发布单篇笔记
  ├─ 切换"上传图文"
  ├─ 上传3张图片
  ├─ 根据当前篇数(1-5)选择内容角度
  ├─ 生成并填写标题、正文、话题
  ├─ 添加商品（根据商品ID）
  └─ 点击发布

Step 7: 记录发布
  ├─ 记录商品X第N篇已发
  ├─ 更新今日计数
  └─ 保存进度

Step 8: 循环决策
  ├─ 该商品是否<5篇？
  │   ├─ 是 → 继续发该商品下一篇（换角度）
  │   └─ 否 → 轮到下一个商品
  ├─ 是否还有<5篇的商品？
  │   ├─ 是 → 下一个商品（如图片未下载→Step 4）
  │   └─ 否 → 所有商品已发满，停止
  └─ 今日是否<50篇？是则继续，否则停止
```

---

## 三阶段脚本使用

本技能提供三个独立的阶段脚本，可分别调度执行。各脚本会自动检查前置条件并按需执行前置阶段。

### 快速开始

```bash
# 进入技能目录
cd ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts

# 方式1：手动按顺序执行
./phase1-prepare.sh   # 准备商品和图片
./phase2-generate.sh  # 生成内容
./phase3-publish.sh   # 发布笔记

# 方式2：直接执行阶段3（自动检查并执行前置阶段）
./phase3-publish.sh
```

### 阶段1：准备商品和图片

**脚本**: `scripts/phase1-prepare.sh`

**职责**：
- 获取最新10个商品
- 进入每个商品详情页
- 下载前3张主图（共30张）
- 保存商品池信息

**输出文件**：
```
~/.openclaw/workspace/xiaohongshu-data/
├── today-pool.json          # 商品池数据
└── images/
    └── {商品ID}/
        ├── 1.jpg
        ├── 2.jpg
        └── 3.jpg
```

**执行**：
```bash
./phase1-prepare.sh
```

---

### 阶段2：生成笔记内容

**脚本**: `scripts/phase2-generate.sh`

**职责**：
- 检查阶段1是否完成（如未完成自动执行）
- 使用 xiaohongshu-mcp skill 搜索并获取20-30篇发夹热门笔记
- 分析热门笔记的标题结构、文案风格、标签使用、emoji技巧
- 结合商品主图特征，模仿热门笔记风格编写5篇不同角度的内容
- 保存内容到文件（不直接发布）

**依赖**：阶段1完成、xiaohongshu-mcp server 运行中

**工作流程**：

```
步骤1: 获取热门笔记参考
  ├─ 调用 xiaohongshu-mcp 搜索"发夹"关键词
  ├─ 获取20-30篇笔记的标题、正文、标签、互动数据
  └─ 按点赞/收藏数排序，筛选高互动爆款

步骤2: 分析爆款特征
  ├─ 提取高频标题结构（如：emoji位置、数字使用、疑问句等）
  ├─ 总结文案风格（语气、段落结构、卖点表达方式）
  ├─ 统计热门标签组合
  └─ 分析emoji使用规律

步骤3: 结合商品编写笔记
  ├─ 读取商品主图（从阶段1的today-pool.json和images目录）
  ├─ 根据5个不同角度（颜值/材质/搭配/风格/体验）
  ├─ 模仿爆款笔记风格，结合图片特征编写内容
  └─ 确保每篇都有独特角度，避免重复
```

**输出文件**：
```
~/.openclaw/workspace/xiaohongshu-data/
├── contents.json              # 50篇笔记内容
├── hot-notes-analysis.json    # 热门笔记分析报告
└── raw-hot-notes.json         # 原始热门笔记数据（20-30篇）
```

**内容格式**：
```json
{
  "hot_notes_analysis": {
    "search_keyword": "发夹",
    "total_collected": 25,
    "title_patterns": ["xx款xx夹，xx感绝了", "发现xx宝藏xx"],
    "top_tags": ["#发夹", "#发饰分享", "#头饰发饰"],
    "emoji_usage": {"高频": ["✨", "💖", "🎀"], "场景": "标题开头或结尾"},
    "content_structure": "痛点引入→产品展示→使用场景→情感升华"
  },
  "contents": {
    "商品ID-1": [
      {
        "angle": 1,
        "angle_name": "颜色颜值",
        "reference_notes": ["note_id_1", "note_id_3"],
        "title": "...",
        "content": "...",
        "tags": "#xxx #xxx"
      },
      ...
    ]
  }
}
```

**内容编写原则**：

| 原则 | 说明 |
|------|------|
| 学习而非复制 | 学习爆款笔记的结构和风格，不直接复制原文 |
| 结合图片事实 | 标题和内容必须基于商品主图可见的特征 |
| 5个不同角度 | 每个商品5篇，分别侧重：颜值/材质/搭配/风格/体验 |
| 爆款化表达 | 运用分析出的高频标题结构、emoji使用、标签组合 |

**前置条件**（重要）：

阶段2依赖 `xiaohongshu-mcp` server 运行，需要用户手动管理登录状态：

```bash
# 1. 首次使用或 session 过期时需要登录
# 运行后扫码登录，生成 cookies.json
cd ~/.openclaw/skills/xiaohongshu-mcp
./xiaohongshu-login-darwin-arm64

# 2. 启动 MCP server（保持运行，另开终端）
./xiaohongshu-mcp-darwin-arm64

# 3. 验证登录状态（显示 ✅ Logged in 表示成功）
python3 scripts/xhs_client.py status
```

> **注意**：cookies 会过期（通常几天到几周），如果阶段2提示未登录，需要重新执行步骤1。

**执行**：
```bash
# 确保 server 已启动并登录成功后执行
./phase2-generate.sh
```

---

### 阶段3：发布笔记

**脚本**: `scripts/phase3-publish.sh`

**职责**：
- 检查阶段1和2是否完成（如未完成自动执行）
- 批量发布50篇笔记
- 支持当天中断恢复
- 记录发布日志

**依赖**：阶段1和2完成

**输出**：发布到小红书 + 本地日志

**执行**：
```bash
./phase3-publish.sh
```

**断点续传**：
- 如果当天已发布23篇后中断
- 重新运行会自动从第24篇继续
- 不会重复发布已完成的笔记

---

## Cron 配置（待验证后启用）

```bash
# 每天早上8点：准备商品和图片
0 8 * * * /bin/bash ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase1-prepare.sh >> /tmp/xhs-phase1.log 2>&1

# 每天上午10点：生成内容
0 10 * * * /bin/bash ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase2-generate.sh >> /tmp/xhs-phase2.log 2>&1

# 每天晚上8点：发布笔记
0 20 * * * /bin/bash ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase3-publish.sh >> /tmp/xhs-phase3.log 2>&1
```

---

## 详细操作步骤（分步说明）

如需自定义或调试，可参考以下分步说明。

### 前置：发布历史管理脚本

**文件**: `~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py`

```python
#!/usr/bin/env python3
"""
发布管理器
- 记录每个商品每天的发帖数量
- 实现10个商品轮流发策略
- 检查每日50篇配额
"""

import json
import os
from datetime import datetime, date

DB_FILE = os.path.expanduser("~/.openclaw/workspace/xiaohongshu-publish-log.json")
PRODUCTS_FILE = os.path.expanduser("~/.openclaw/workspace/xiaohongshu-products.json")

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"daily_logs": {}, "product_rotation": {}}

def save_db(db):
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_today_count():
    """获取今日已发布数量"""
    db = load_db()
    today = str(date.today())
    return db["daily_logs"].get(today, {}).get("total", 0)

def get_product_today_count(product_id):
    """获取某个商品今日已发布数量"""
    db = load_db()
    today = str(date.today())
    return db["daily_logs"].get(today, {}).get("products", {}).get(product_id, 0)

def record_publish(product_id, product_name, note_title):
    """记录一次发布"""
    db = load_db()
    today = str(date.today())
    
    if today not in db["daily_logs"]:
        db["daily_logs"][today] = {"total": 0, "products": {}, "notes": []}
    
    db["daily_logs"][today]["total"] += 1
    db["daily_logs"][today]["products"][product_id] = db["daily_logs"][today]["products"].get(product_id, 0) + 1
    db["daily_logs"][today]["notes"].append({
        "product_id": product_id,
        "product_name": product_name,
        "title": note_title,
        "time": datetime.now().isoformat()
    })
    
    save_db(db)
    return {"success": True}

def can_publish():
    """检查是否可以发布"""
    today_count = get_today_count()
    if today_count >= 50:
        return {"can": False, "reason": "今日已达50篇上限", "today_count": today_count}
    return {"can": True, "today_count": today_count, "remaining": 50 - today_count}

def select_product_strategy(top10_products):
    """
    选择下一个要发的商品
    策略：在最新10个商品中，选择今日发帖数<5的商品，优先发帖少的
    """
    db = load_db()
    today = str(date.today())
    
    # 筛选今日可发的商品（<5篇）
    available = []
    for p in top10_products:
        pid = p.get("id")
        count = get_product_today_count(pid)
        if count < 5:
            available.append({
                **p,
                "today_count": count,
                "remaining": 5 - count
            })
    
    if not available:
        return {"error": "今日所有商品都已发满5篇"}
    
    # 优先选择发帖数少的商品（实现轮流）
    available.sort(key=lambda x: x["today_count"])
    selected = available[0]
    
    return {
        "selected": selected,
        "available_count": len(available),
        "today_total": get_today_count()
    }

def get_stats():
    """获取统计信息"""
    db = load_db()
    today = str(date.today())
    today_data = db["daily_logs"].get(today, {"total": 0, "products": {}})
    
    return {
        "today_total": today_data["total"],
        "today_remaining": 50 - today_data["total"],
        "today_products": today_data["products"]
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["can-publish", "today-count", "product-count", "record", "select", "stats"])
    parser.add_argument("--product-id", help="商品ID")
    parser.add_argument("--product-name", help="商品名称")
    parser.add_argument("--title", help="笔记标题")
    parser.add_argument("--products-json", help="商品列表JSON（select命令使用）")
    args = parser.parse_args()
    
    if args.command == "can-publish":
        print(json.dumps(can_publish(), ensure_ascii=False))
    elif args.command == "today-count":
        print(json.dumps({"today_count": get_today_count()}, ensure_ascii=False))
    elif args.command == "product-count":
        print(json.dumps({"count": get_product_today_count(args.product_id)}, ensure_ascii=False))
    elif args.command == "record":
        print(json.dumps(record_publish(args.product_id, args.product_name, args.title), ensure_ascii=False))
    elif args.command == "select":
        products = json.loads(args.products_json or "[]")
        print(json.dumps(select_product_strategy(products), ensure_ascii=False))
    elif args.command == "stats":
        print(json.dumps(get_stats(), ensure_ascii=False))
```

---

### 步骤1: 检查每日配额

```bash
# 检查今日是否还能发布
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py can-publish

# 输出示例：
# {"can": true, "today_count": 12, "remaining": 38}
# 或
# {"can": false, "reason": "今日已达50篇上限", "today_count": 50}
```

---

### 步骤2: 恢复登录状态

```bash
# 1. 停止现有浏览器
agent-browser stop 2>/dev/null || true
sleep 2

# 2. 加载登录状态
agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json

# 3. 打开商品管理页面
agent-browser open https://ark.xiaohongshu.com/app-item/list/shelf

# 4. 等待加载
agent-browser wait 3000
```

---

### 步骤3: 初始化今日商品池并确定发布位置

```bash
# 1. 提取商品列表（按上架时间排序，取前10个）
agent-browser eval "
const rows = document.querySelectorAll('tr');
const products = [];
rows.forEach(row => {
  const cells = row.querySelectorAll('td');
  if (cells.length > 5) {
    const infoCell = cells[1];
    const timeCell = cells[3]; // 上架时间列
    if (infoCell && timeCell) {
      const idMatch = infoCell.textContent.match(/商品ID[：:]\\s*(\\w+)/);
      const nameMatch = infoCell.textContent.match(/^([^商品ID]+)/);
      const timeText = timeCell.textContent.trim();
      if (idMatch) {
        products.push({
          id: idMatch[1],
          name: nameMatch ? nameMatch[1].trim().substring(0, 30) : '',
          createTime: timeText
        });
      }
    }
  }
});
// 按上架时间排序（最新的在前）
products.sort((a, b) => new Date(b.createTime) - new Date(a.createTime));
JSON.stringify(products.slice(0, 10), null, 2);
" > /tmp/top10_products.json

# 2. 检查今日是否已有商品池（中断恢复）
POOL_STATUS=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-pool)

if echo "$POOL_STATUS" | grep -q '"pool": \[\]'; then
  # 今天第一次运行，初始化商品池
  echo "🆕 初始化今日商品池..."
  python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py init-pool --products-json "$(cat /tmp/top10_products.json)"
else
  echo "📋 恢复今日商品池（中断恢复）"
  echo "$POOL_STATUS"
fi

# 3. 获取当前应该发哪个商品的第几篇（轮流策略）
POSITION=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-position)
echo "当前位置: $POSITION"

# 4. 提取商品信息
PRODUCT_ID=$(echo "$POSITION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('product',{}).get('id',''))")
PRODUCT_NAME=$(echo "$POSITION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('product',{}).get('name',''))")
NEXT_ANGLE=$(echo "$POSITION" | python3 -c "import sys,json; print(json.load(sys.stdin).get('next_angle',1))")
REMAINING=$(echo "$POSITION" | python3 -c "import sys,json; print(json.load(sys.stdin).get('remaining',5))")

echo "🎯 目标商品: $PRODUCT_NAME (ID: $PRODUCT_ID)"
echo "📝 第${NEXT_ANGLE}篇（还剩${REMAINING}篇）"

---

### 步骤4: 进入商品详情页下载图片

```bash
# 1. 点击商品名称进入详情页
agent-browser eval "
const rows = document.querySelectorAll('tr');
rows.forEach(row => {
  const cells = row.querySelectorAll('td');
  if (cells.length > 1) {
    const infoCell = cells[1];
    if (infoCell && infoCell.textContent.includes('${PRODUCT_ID}')) {
      // 点击商品名称链接
      const link = infoCell.querySelector('a');
      if (link) {
        link.click();
        console.log('CLICKED_PRODUCT_DETAIL');
      }
    }
  }
});
"

# 2. 等待详情页加载
agent-browser wait 3000

# 3. 验证URL包含商品ID
current_url=$(agent-browser eval "console.log(window.location.href)" | tail -1)
echo "当前页面: $current_url"

# 4. 提取商品主图URL（详情页通常有更多图片）
agent-browser eval "
const uniqueUrls = new Set();
const images = [];
// 查找 qimg.xiaohongshu.com 域名的图片
document.querySelectorAll('img').forEach(img => {
  if (img.src && img.src.includes('qimg.xiaohongshu.com')) {
    const baseUrl = img.src.split('?')[0];
    if (!uniqueUrls.has(baseUrl)) {
      uniqueUrls.add(baseUrl);
      images.push(img.src);
    }
  }
});
JSON.stringify(images.slice(0, 3), null, 2);
" > /tmp/product_images.json

echo "提取的图片:"
cat /tmp/product_images.json

# 5. 创建商品图片目录并下载
mkdir -p ~/.openclaw/workspace/product-images/${PRODUCT_ID}

# 下载前3张图片
python3 << PYEOF
import json
import urllib.request
import os

with open('/tmp/product_images.json', 'r') as f:
    images = json.load(f)

for i, url in enumerate(images[:3], 1):
    try:
        path = f"~/.openclaw/workspace/product-images/${PRODUCT_ID}/{i}.jpg"
        path = os.path.expanduser(path)
        urllib.request.urlretrieve(url, path)
        print(f"Downloaded {i}.jpg")
    except Exception as e:
        print(f"Error downloading {i}: {e}")
PYEOF

# 6. 返回商品管理页面
agent-browser open https://ark.xiaohongshu.com/app-item/list/shelf
agent-browser wait 3000
```

---

### 步骤5: 点击"去发布"

```bash
# 找到对应商品并点击"去发布"
agent-browser eval "
const rows = document.querySelectorAll('tr');
rows.forEach(row => {
  if (row.textContent.includes('${PRODUCT_ID}')) {
    const publishBtn = Array.from(row.querySelectorAll('span, a, button')).find(el => el.textContent.trim() === '去发布');
    if (publishBtn) {
      publishBtn.click();
      console.log('CLICKED_PUBLISH');
    }
  }
});
"

agent-browser wait 3000
```

---

### 步骤6: 上传图文

```bash
# 1. 切换到图文模式
agent-browser eval "
const tab = Array.from(document.querySelectorAll('*')).find(e => e.textContent && e.textContent.trim() === '上传图文');
if (tab) { tab.click(); console.log('OK'); }
"
agent-browser wait 2000

# 2. 上传3张图片（从商品详情下载的）
for i in 1 2 3; do
  IMG_PATH="$HOME/.openclaw/workspace/product-images/${PRODUCT_ID}/${i}.jpg"
  if [ -f "$IMG_PATH" ]; then
    echo "上传图片 $i..."
    agent-browser upload @ChooseFile "$IMG_PATH" || echo "Upload $i may timeout but continue"
    agent-browser wait 2000
  fi
done
```

---

### 步骤7: 生成并填写内容（5个不同角度）

```bash
# 确定当前是第几篇（1-5）
CURRENT_COUNT=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py product-count --product-id "${PRODUCT_ID}" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
ANGLE_NUM=$((CURRENT_COUNT + 1))

echo "当前是第 $ANGLE_NUM 篇笔记"

# 根据篇数选择不同角度
ANGLES=(
  "日常百搭，突出发夹的可爱和实用性"
  "性价比，突出价格便宜适合学生党"
  "搭配教程，教用户怎么搭配发型"
  "使用场景，通勤约会都能戴"
  "真实测评，使用一周的真实感受"
)

ANGLE="${ANGLES[$((ANGLE_NUM - 1))]}"

echo "内容角度: $ANGLE"

# 使用子代理生成内容
generated=$(sessions_spawn --runtime=subagent --mode=run --runTimeoutSeconds=30 "
为小红书发夹商品生成一篇笔记。

商品信息：${PRODUCT_NAME}
内容角度：${ANGLE}

要求：
1. 标题：20字以内，吸引人，符合小红书风格
2. 正文：100-200字，真诚推荐，带emoji
3. 标签：5-8个相关话题标签

输出格式：
标题：xxx
正文：xxx
标签：#xxx #xxx
")

# 解析生成结果（简化版，实际需要更好的解析）
TITLE=$(echo "$generated" | grep "标题：" | sed 's/标题：//')
CONTENT=$(echo "$generated" | sed -n '/正文：/,/标签：/p' | grep -v "标签：" | sed 's/正文：//')
TAGS=$(echo "$generated" | grep "标签：")

echo "生成标题: $TITLE"
```

**填写内容**：

```bash
# 填写标题
agent-browser eval "
const t = document.querySelector('input[placeholder*=\"标题\"]');
if (t) { 
  t.value = '${TITLE}'; 
  t.dispatchEvent(new Event('input', { bubbles: true }));
}
"

# 填写正文
agent-browser eval "
const c = document.querySelector('textarea');
if (c) { 
  c.value = '${CONTENT}'; 
  c.dispatchEvent(new Event('input', { bubbles: true }));
}
"

# 添加话题
agent-browser eval "
const b = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('话题'));
if (b) b.click();
"
agent-browser wait 1000

# 选择"发夹"话题（可根据内容动态选择）
agent-browser eval "
const items = document.querySelectorAll('li');
items.forEach(i => { 
  if (i.textContent.includes('发夹')) i.click(); 
});
"
```

---

### 步骤8: 添加商品

```bash
# 1. 点击"添加商品"
agent-browser eval "
const b = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('添加商品'));
if (b) b.click();
"
agent-browser wait 2000

# 2. 根据商品ID选择
agent-browser eval "
const rows = document.querySelectorAll('div, tr, li');
rows.forEach(r => {
  if (r.textContent && r.textContent.includes('${PRODUCT_ID}')) {
    const cb = r.querySelector('input[type=\"checkbox\"]');
    if (cb) { 
      cb.click(); 
      cb.checked = true;
      console.log('PRODUCT_SELECTED');
    }
  }
});
"

# 3. 点击保存
agent-browser eval "
const s = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '保存');
if (s) s.click();
"
agent-browser wait 2000
```

---

### 步骤9: 发布并记录

```bash
# 1. 点击发布
agent-browser eval "
const p = Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.trim() === '发布');
if (p) { p.click(); console.log('PUBLISHED'); }
"
agent-browser wait 5000

# 2. 记录发布（带上angle参数，表示这是第几篇）
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py record \
  --product-id "${PRODUCT_ID}" \
  --product-name "${PRODUCT_NAME}" \
  --title "${TITLE}" \
  --angle ${NEXT_ANGLE}

# 3. 检查今日统计
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py stats

# 4. 检查是否继续
CAN_PUBLISH=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py can-publish)
echo "$CAN_PUBLISH"

# 如果还能发，获取下一个位置继续
if echo "$CAN_PUBLISH" | grep -q '"can": true'; then
  NEXT_POS=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-position)
  if ! echo "$NEXT_POS" | grep -q '"error"'; then
    echo "🔄 继续发下一篇..."
    # 循环回到 Step 5（根据情况决定是否需要下载新商品图片）
  fi
fi
```

---

### 步骤10: 完整每日发布脚本（支持中断恢复）

```bash
#!/bin/bash
# daily-publish.sh - 完整每日发布流程
# 策略：当天锁定10个商品，轮流发，每个发5篇，支持中断恢复

echo "🐟 小红书每日发布开始"

# ========== Step 1: 检查配额 ==========
echo "📊 检查今日配额..."
CAN_PUBLISH=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py can-publish)
if echo "$CAN_PUBLISH" | grep -q '"can": false'; then
  echo "✅ $(echo "$CAN_PUBLISH" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")"
  exit 0
fi

REMAINING=$(echo "$CAN_PUBLISH" | python3 -c "import sys,json; print(json.load(sys.stdin)['remaining'])")
echo "今日还可发布: $REMAINING 篇"

# ========== Step 2: 恢复登录 ==========
echo "📱 恢复登录状态..."
agent-browser stop 2>/dev/null || true
sleep 2
agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json
agent-browser open https://ark.xiaohongshu.com/app-item/list/shelf
agent-browser wait 3000

# ========== Step 3: 初始化/恢复商品池 ==========
POOL_STATUS=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-pool)

if echo "$POOL_STATUS" | grep -q '"pool": \[\]'; then
  # 今天第一次运行，需要初始化商品池
  echo "📋 初始化今日商品池..."
  
  # 提取最新10个商品
  agent-browser eval "
const rows = document.querySelectorAll('tr');
const products = [];
rows.forEach(row => {
  const cells = row.querySelectorAll('td');
  if (cells.length > 5) {
    const infoCell = cells[1];
    const timeCell = cells[3];
    if (infoCell && timeCell) {
      const idMatch = infoCell.textContent.match(/商品ID[：:]\\s*(\\w+)/);
      const nameMatch = infoCell.textContent.match(/^([^商品ID]+)/);
      const timeText = timeCell.textContent.trim();
      if (idMatch) {
        products.push({
          id: idMatch[1],
          name: nameMatch ? nameMatch[1].trim().substring(0, 30) : '',
          createTime: timeText
        });
      }
    }
  }
});
products.sort((a, b) => new Date(b.createTime) - new Date(a.createTime));
JSON.stringify(products.slice(0, 10), null, 2);
" > /tmp/today_products.json

  python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py init-pool \
    --products-json "$(cat /tmp/today_products.json)"
  
  echo "🆕 今日商品池已创建"
else
  echo "📋 恢复今日商品池（中断恢复）"
fi

# 显示今日商品池
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py stats

# ========== Step 4: 循环发布 ==========
while true; do
  # 检查配额
  CAN_PUB=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py can-publish)
  if echo "$CAN_PUB" | grep -q '"can": false'; then
    echo "✅ 今日配额已满，发布完成！"
    break
  fi
  
  # 获取当前发布位置
  POSITION=$(python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-position)
  
  if echo "$POSITION" | grep -q '"error"'; then
    echo "✅ 今日所有商品已发满，发布完成！"
    break
  fi
  
  PRODUCT_ID=$(echo "$POSITION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('product',{}).get('id',''))")
  PRODUCT_NAME=$(echo "$POSITION" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('product',{}).get('name',''))")
  NEXT_ANGLE=$(echo "$POSITION" | python3 -c "import sys,json; print(json.load(sys.stdin).get('next_angle',1))")
  
  echo ""
  echo "🎯 当前: $PRODUCT_NAME - 第${NEXT_ANGLE}篇"
  
  # 检查是否需要下载图片（首次）
  IMG_DIR="$HOME/.openclaw/workspace/product-images/${PRODUCT_ID}"
  if [ ! -d "$IMG_DIR" ] || [ ! -f "$IMG_DIR/1.jpg" ]; then
    echo "📥 下载商品图片..."
    # ... 进入商品详情页下载图片 ...
  fi
  
  # 执行单篇发布（复用之前的步骤5-9）
  # ... 发布逻辑 ...
  
  echo "✅ 发布成功"
  sleep 3
done

# ========== Step 5: 输出统计 ==========
echo ""
echo "📊 今日发布统计："
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py stats

echo ""
echo "🐟 所有任务完成！"
```

---

## 飞书通知模板

### 每日发布完成
```
🐟 小红书今日发布完成

📊 发布统计：
├─ 今日已发：X / 50 篇
├─ 剩余配额：Y 篇
└─ 涉及商品：N 个

📦 商品分布：
├─ 商品1：5篇 ✅
├─ 商品2：5篇 ✅
├─ 商品3：3篇（还差2篇）
└─ ...

⏰ 下次可发：明天 00:00 后
```

### 单篇发布成功
```
✅ 笔记发布成功

📝 标题：xxx
📦 商品：xxx（第 N/5 篇）
🏷️ 标签：#xxx #xxx
⏰ 时间：2026-03-03 14:30
📊 今日累计：X / 50 篇
```

---

## 测试记录与已知问题

### 2026-03-04 测试记录

**阶段1（准备商品和图片）**: ✅ 成功
- 成功获取10个商品
- 成功下载30张商品主图

**阶段2（生成内容）**: ✅ 成功
- 成功生成50篇笔记内容
- 已保存到 `contents.json`

**阶段3（发布笔记）**: ⚠️ 部分成功，遇到以下问题

#### 已知问题 #1: 正文 textarea 超时
**症状**: 
```
Action on "textarea[placeholder*='输入正文']" timed out. 
The element may be blocked, still loading, or not interactable.
```

**原因**: 
- 小红书发布页面的 textarea 可能使用动态加载或 iframe
- 简单的 CSS 选择器可能无法定位到实际的可编辑区域

**当前解决方案** (phase3-publish.sh 已采用):
```bash
# 使用更通用的 textarea 选择器，配合 JavaScript 直接操作
agent-browser eval "
  const ta = document.querySelector('textarea');
  if (ta) {
    ta.value = \"$CONTENT_ESCAPED\";
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
" 2>/dev/null || true
```

**已实施的改进** (2026-03-05):
- [x] 修复发布流程 - 添加"手动创作"点击步骤
- [x] 修复图片上传 - 使用 base64 + DataTransfer 方式
- [x] 修复正文填写 - 使用 contenteditable 元素
- [x] 修复标题填写 - 使用 placeholder 定位
- [x] 修复话题添加 - 使用标签搜索+点击方式
- [x] 修复发布验证 - 通过 URL 跳转判断成功

**测试结果**: ✅ 2026-03-05 测试成功，完整发布流程已跑通

**正确的发布流程**:
1. 打开发布页面 (自动关联商品)
2. 点击"手动创作" (如果显示智能创作界面)
3. 点击"上传图文" tab
4. 上传图片 (base64 + DataTransfer 方式)
5. 填写标题 (placeholder="填写标题会有更多赞哦")
6. 填写正文 (contenteditable 元素)
7. 添加话题 (可选)
8. 点击发布
9. 验证成功 (URL 跳转到 note-list)

#### 已知问题 #2: 页面加载状态不稳定
**症状**: 有时页面加载后元素还未完全渲染

**解决方案**:
- 已增加 `agent-browser wait` 在各步骤之间
- 发布前等待 10 秒确保页面完全加载

---

### 2026-03-03 测试记录
- 基本流程验证通过
- 待补充：批量发布逻辑、10商品轮流策略、5篇/商品限制

---

## 发布流程调试指南

### 如果阶段3遇到问题

1. **手动检查页面状态**:
```bash
# 加载状态并打开发布页面
agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json
agent-browser open "https://ark.xiaohongshu.com/app-note/publish?source=note-list"
agent-browser wait 5000

# 获取页面快照，查看元素是否存在
agent-browser snapshot
```

2. **手动定位正文输入框**:
```bash
# 尝试不同的选择器
agent-browser eval "document.querySelectorAll('textarea').length"
agent-browser eval "document.querySelectorAll('[contenteditable=true]').length"
```

3. **检查当前 URL**:
```bash
agent-browser get url
```

---

## 故障排除

### 阶段3发布失败排查步骤

#### 问题1: 正文填写超时/失败

**诊断命令**:
```bash
# 1. 手动加载状态
agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json

# 2. 打开发布页面
agent-browser open "https://ark.xiaohongshu.com/app-note/publish?source=note-list"
agent-browser wait 5000

# 3. 检查页面元素
agent-browser eval "
  console.log('Textarea count:', document.querySelectorAll('textarea').length);
  console.log('Contenteditable count:', document.querySelectorAll('[contenteditable=true]').length);
  console.log('URL:', window.location.href);
"

# 4. 如果 textarea 不存在，截图查看
agent-browser screenshot /tmp/xhs_debug.png
```

**常见原因**:
- 页面未完全加载 → 增加 wait 时间
- 使用 iframe → 需要切换 frame
- 使用富文本编辑器 → 需要使用 contenteditable
- 登录状态过期 → 需要重新登录

#### 问题2: 发布按钮点击无效

**诊断命令**:
```bash
# 检查按钮状态
agent-browser eval "
  const buttons = document.querySelectorAll('button');
  buttons.forEach(btn => {
    if (btn.textContent.includes('发布')) {
      console.log('发布按钮:', btn.disabled ? '禁用' : '可用', btn.textContent);
    }
  });
"
```

**可能原因**:
- 必填项未填写完整（标题/正文/图片）
- 图片上传中，按钮被禁用
- 页面报错，需要查看控制台

#### 问题3: 登录状态失效

**症状**: 页面跳转到登录页或显示未登录

**解决**:
```bash
# 1. 重新登录
cd ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts
./login-helper.sh

# 2. 验证登录
agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json
agent-browser open "https://ark.xiaohongshu.com/app-item/list/shelf"
agent-browser wait 3000
agent-browser eval "document.title"
# 应该显示"商品管理"之类的标题，而不是"登录"
```

---

## 依赖技能

- [Agent Browser 技能文档](../../agent-browser/SKILL.md)
- [xiaohongshu-mcp 技能文档](../../xiaohongshu-mcp/SKILL.md)
