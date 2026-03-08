# 小红书商品笔记自动发布 - 快速参考

## 三阶段工作模式

```
阶段1（准备）      阶段2（内容）        阶段3（发布）
    │                │                  │
    ▼                ▼                  ▼
获取10个商品  →  生成50篇内容  →  批量发布笔记
下载30张图片      （LLM+Tavily）    （支持断点续传）
```

## 阶段脚本

### 阶段1：准备商品和图片
```bash
~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase1-prepare.sh

# 输出
# - ~/.openclaw/workspace/xiaohongshu-data/today-pool.json
# - ~/.openclaw/workspace/xiaohongshu-data/images/{商品ID}/{1,2,3}.jpg
```

### 阶段2：生成笔记内容
```bash
~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase2-generate.sh

# 依赖：阶段1完成
# 使用Tavily搜索热门笔记作为参考
# 为每个商品生成5篇不同角度内容

# 输出
# - ~/.openclaw/workspace/xiaohongshu-data/contents.json
```

### 阶段3：发布笔记
```bash
~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase3-publish.sh

# 依赖：阶段1和阶段2完成
# 支持断点续传（当天中断可恢复）
```

## 手动执行完整流程

```bash
# 1. 准备（早上）
./phase1-prepare.sh

# 2. 生成内容（上午）
./phase2-generate.sh

# 3. 发布（晚上）
./phase3-publish.sh
```

## Cron 配置（待验证后配置）

```bash
# 每天早上8点：准备商品和图片
0 8 * * * /bin/bash /Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase1-prepare.sh >> /tmp/xhs-phase1.log 2>&1

# 每天上午10点：生成内容
0 10 * * * /bin/bash /Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase2-generate.sh >> /tmp/xhs-phase2.log 2>&1

# 每天晚上8点：发布笔记
0 20 * * * /bin/bash /Users/levin/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/phase3-publish.sh >> /tmp/xhs-phase3.log 2>&1
```

## 数据文件

```
~/.openclaw/workspace/xiaohongshu-data/
├── today-pool.json          # 阶段1输出：今日商品池
├── images/                  # 商品图片
│   └── {商品ID}/
│       ├── 1.jpg
│       ├── 2.jpg
│       └── 3.jpg
├── contents.json            # 阶段2输出：生成的内容
└── tavily-search-results.json  # Tavily搜索结果
```

## 辅助脚本（publish-manager.py）

```bash
# 检查今日配额
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py can-publish

# 查看今日统计
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py stats

# 查看当前发布位置（断点恢复）
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py get-position

# 重置今日记录（谨慎）
python3 ~/.openclaw/workspace/skills/xiaohongshu-product-poster/scripts/publish-manager.py reset-today
```

## 故障排除

### 阶段1失败
- 检查登录状态：`agent-browser state load ~/.openclaw/workspace/xiaohongshu-auth.json`
- 删除商品池重新执行：`rm ~/.openclaw/workspace/xiaohongshu-data/today-pool.json`

### 阶段2失败
- 检查阶段1是否完成：`ls ~/.openclaw/workspace/xiaohongshu-data/today-pool.json`
- 检查Tavily搜索是否正常

### 阶段3失败
- 检查阶段1和2是否完成
- 检查今日配额：`publish-manager.py can-publish`
- 中断后重新运行会自动恢复

## 内容生成说明

阶段2使用动态内容生成策略：
- 使用Tavily搜索当天热门发夹笔记
- LLM根据搜索结果自主决定每篇内容角度
- 不固定角度模板，保证内容多样性

## 完整文档

- `SKILL.md` - 完整技能文档
- `QUICKREF.md` - 本快速参考
