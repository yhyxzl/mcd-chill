# WorkBuddy 开发对话上下文

本文件用于麦当劳程序员创意开发大赛的 **WorkBuddy 专项奖励**核验，
记录本项目在腾讯 WorkBuddy 中的实际开发过程。

---

## 一、使用的产品

| 项 | 值 |
|---|---|
| 产品 | 腾讯 WorkBuddy |
| 使用方式 | WorkBuddy 桌面客户端，Agent 模式 |
| 会话日期 | 2026-10-09 |
| 工作目录 | 本地 Git 仓库 `mcd-chill` |

项目**全部代码、文档与联调调试均在 WorkBuddy 中完成**，没有使用其他 AI 编程工具。

---

## 二、开发过程实录

### 1. 赛前情报收集

在 WorkBuddy 中读取赛事仓库，确认了三项硬性报名要求
（仓库必须公开、必须含 README.md / CONTEST_DECLARATION.md / MCP_INTEGRATION.md、
Issue 需按标准格式），并抓取官方排行榜快照分析竞争态势。

### 2. MCP 打通与联调

WorkBuddy 直接对 `https://mcp.mcd.cn` 发起真实调用：

- `initialize` 握手，确认 `serverInfo` 为 `mcd-mcp 1.0.0`，协议版本 `2025-03-26`
- `tools/list` 拿到 **35 个工具**（官方 README 只列了 34 个，实际多出
  `query-promotions` 与 `query-survey-coupon`）
- 逐个实测 8 个只读工具，把真实响应落盘到 `probe_out/` 用于校准解析逻辑

### 3. 在联调中修掉的四个真实 Bug

这几个问题都是 WorkBuddy 在跑真实调用时暴露出来的，靠读文档发现不了：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 首次请求必定 `WinError 10060` 超时 | 网络首连抖动 | 客户端加指数退避重试（3 次） |
| 2 | 营养库解析出 0 条 | 先从 `content[0].text` 取到了「字段说明」长文本，没下钻到 `structuredContent` | 改为按表头特征 `\[\d+\]\{[^}]+\}:` 做广度搜索定位真正的表格 |
| 3 | 表头正则匹配不上 | `^...$` 在多行字符串里只锚定首尾 | 正则加 `re.MULTILINE`，检测用独立的无锚点 `TABLE_MARK_RE` |
| 4 | 替换建议出现「巨无霸 → 炸鸡翅」 | 品类关键词表覆盖不足，「巨无霸」被归为 other，落入兜底池 | 按真实 160 条餐品名单重写品类规则，并把 `other` 改为**不给建议**而非兜底 |

### 4. 文档与交付

WorkBuddy 完成了 README、MCP_INTEGRATION.md、SKILL.md 与本文件的撰写，
并按官方要求逐字节复制了 `CONTEST_DECLARATION.md`（不可改动）。

---

## 三、WorkBuddy 起到的关键作用

1. **零成本打通真实接口** —— 直接在本地发起 MCP 调用、解析响应、迭代解析器，
   省掉了"手写客户端 → 装 SDK → 调试"的全过程。
2. **真实数据驱动的设计修正** —— 通过实测发现营养库只有 160 条且饮品占大头、
   `query-meals` 只返回 code 不返回名称、
   `query-nearby-stores` 冷启动拿不到门店（进而设计出「从历史订单反推门店」的兜底链路），
   这些都是凭文档设计不出来的。
3. **确定性代码而非提示词** —— WorkBuddy 把判决逻辑落成了可测试的 Python 规则引擎
   （`src/verdict.py`），而不是一段提示词，保证了结果可复现。

---

## 四、复现方式

```bash
export MCD_MCP_TOKEN="<你的麦当劳 MCP Token>"

# 拉取只读工具真实响应
python scripts/probe.py

# 运行冷静期体检
python src/cli.py 巨无霸 中薯条 可乐中杯
```
