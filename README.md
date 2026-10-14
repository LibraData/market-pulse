# A 股市场温度计 · market-pulse

[![数据更新工作流状态](https://github.com/LibraData/market-pulse/actions/workflows/update.yml/badge.svg?branch=main)](https://github.com/LibraData/market-pulse/actions/workflows/update.yml)

每天收盘后，从指数强度、涨跌广度、成交量能与均线位置观察 A 股情绪的静态看板。

[打开在线看板](https://LibraData.github.io/market-pulse/) · [查看历史 JSON](public/history.json) · [查看运行记录](https://github.com/LibraData/market-pulse/actions) · [分析短文草稿](analysis-draft.md) · [故障演练清单](failure-drill.md)

**交付状态（2026-10-13）：**取数、JSON 历史保存、Actions、前端展示、故障拦截与恢复、GitHub Pages 公开部署均已验证；目前 2 条行情记录，最新日期为 2026-10-13。徽章表示工作流执行状态，绿色不代表当天一定产生新记录。

## 问题

单一指数涨跌不能完整描述市场冷暖：指数跌幅较小，也可能伴随大面积个股下跌；成交额增加，也不必然说明情绪改善。本项目将四种观察角度合成一个可拆解的情绪分，同时保留原始依据、缺失原因和数据日期，让读者能检查分数从何而来。

## 方法

Python 3.11 使用 AKShare 与 pandas 分段取数，先核验交易日和带日期的上证日线，再计算情绪子分。GitHub Actions 在临时目录生成候选 JSON，校验通过后仅提交 `public/history.json`。前端为纯 HTML、CSS、JavaScript，通过 CDN 加载 ECharts，只读同目录 JSON，无后端、无数据库，也不需要自行配置服务密钥。

## 结论

2026-10-12 的首条记录显示，上证收盘 3,797.2424 点，四指数涨跌幅中位数为 −1.3555%，上涨 1,166 家、下跌 4,325 家。强度子分 −27.11，广度子分约 −57.5305；量能基准不足而缺失，位置按约定暂为 0，固定权重合计约 −22.5156，页面显示 −22.52、偏冷（暂定）。这个结果说明当日广度比上证单一跌幅更弱；只有一条记录，不能据此判断趋势，更不能当作预测或买卖信号。

## 指标口径

```text
S = 0.30 × 强度 + 0.25 × 广度 + 0.25 × 量能 + 0.20 × 位置
```

所有子分限制在 −100 至 +100。实际缩放参数属于方法定义，不是经过收益检验的最优参数。

| 维度 | 权重 | 计算方式 | 不足或失效时 |
| --- | --- | --- | --- |
| 强度 | 30% | 上证、深成、创业板、沪深 300 的涨跌幅中位数 ×20；±5% 对应 ±100 | 四指数不齐时为 `null`，不缩小指数篮子 |
| 广度 | 25% | `[上涨/(上涨+下跌) − 0.5] ×200` | 上涨与下跌合计为 0，或明细维度失效时为 `null` |
| 量能 | 25% | `sign(四指数涨跌幅中位数) × (今日成交额/此前20日均额) ×50`，再截断到 ±100 | 缺少方向、今日成交额或完整基准时为 `null` |
| 位置 | 20% | `(上证收盘/MA20 − 1) ×100 ×5`，再截断到 ±100 | 本地历史不足时按约定为 `0`，并保存原因 |

### 市场范围与单位

- 广度与成交额使用新浪接口返回的**沪深京 A 股**明细，包含北交所。代码按实际返回的市场前缀识别，不将指数成交额相加，避免指数成份重叠导致重复统计。
- 股票零成交行不参与广度；有成交但涨跌幅为 0 的股票记为平盘，不进入上涨加下跌的分母。零成交行对总成交额的贡献为 0。
- 涨跌幅使用百分数值，例如 `−0.434` 表示 `−0.434%`，不是 `−43.4%`。成交额存储单位为元，页面换算为万亿元。
- 量能比值为 1 且方向非零时，子分为 ±50；比值达到 2 或更高时达到对应方向的上限。方向为 0 时该子分为 0。缩量不会自动反转涨跌方向。

### 历史窗口与缺失值

量能基准要求本地存在**此前 20 个交易日**的完整记录，且对应成交额均为正；不以“最近 20 条任意记录”替代。日期由交易日历判断，漏掉一个交易日会使基准暂时不可用。今日成交额不进入基准，同一天重跑也不进入此前窗口。

MA20 含今日收盘与此前 19 个交易日收盘。当前脚本在此前 20 个交易日记录齐全后才启用位置项，首个可完整计算的记录最早是第 21 个连续交易日。在此之前位置子分为 0，`sh_ma20` 为 `null`；这个 0 不是“收盘恰好位于 MA20”的实测结论。

缺失子分以 `null` 输出，总分仅相加可用项的**原固定权重**，不重新归一化。`score_complete=false` 表示至少一项子分缺失；它不表示位置项已经脱离历史不足状态，位置状态还应检查 `missing_reasons.position`。缺失期间的分数与完整分数不宜直接比较，分档按暂定展示。

### 分档边界

| 情绪分 | 分档 |
| --- | --- |
| S > 60 | 过热 |
| 20 ≤ S ≤ 60 | 偏暖 |
| −20 ≤ S < 20 | 中性 |
| −60 ≤ S < −20 | 偏冷 |
| S < −60 | 恐慌 |

连续记录表示截至最新保存日期连续覆盖的交易日数，不是程序执行次数，也不是自然日天数。

## 更新与历史保护

计划于北京时间每周一至周五 **18:17** 执行；Cron 使用 UTC `17 10 * * 1-5`。交易所休市日由脚本跳过。支持 Actions 页手动触发；修改 ETL、依赖或工作流也会触发检查。定时任务仅在默认分支工作，GitHub 可能延迟执行，不能保证准点。

每段取数首次失败后最多重试 3 次，间隔为 5、10、20 秒。每次接口成功返回都会先打印列名及前两行，避免接口变化被悄悄忽略。日线日期接口的 `date`、`close` 列已经通过本地真实输出核对。

- 交易日历失败或未覆盖当天：运行失败，不写历史。
- 非交易日、北京时间 16:00 前、日线尚未更新到当天、全零行情或日期核验不一致：正常跳过，不写历史。
- 单个可选维度失败：保留其他可用维度并注明缺失，不捏造数据。交易日与日期核验属于必须通过的前置条件。
- 日期去重：同日只保留最后写入的版本；过去交易日记录不能被 CI 修改或删除。
- 本地写入：先备份旧文件到 `work/backups/`，再写临时文件并原子替换。首次没有旧文件可备份。
- CI 写入：在 `work/candidate/` 计算与备份，候选校验通过后才复制并提交历史；取数或校验失败不会推送候选数据。
- 日志和旧历史备份作为 Actions artifact 保留 7 天；Git 提交记录另保留已提交的历史版本。不强制推送，远端提交冲突时安全失败。

绿色工作流可能对应正常跳过；需要查看日志中的 `SKIP` 或“写入成功”，以及 JSON 的 `data_date`，确认是否真的更新。分段容错产生的缺失记录也可能正常提交。

## 本地运行

从项目根目录打开 PowerShell，要求已经安装 Python 3.11。

创建项目环境并安装依赖（已有 `.venv` 时可省略创建）：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

先试算，不写 JSON：

```powershell
.\.venv\Scripts\python.exe -u .\src\etl.py --date-source A --date-column date --close-column close --dry-run
```

正式更新：

```powershell
.\.venv\Scripts\python.exe -u .\src\etl.py --date-source A --date-column date --close-column close
```

首次接入或更换日期接口，先探测实际列名：

```powershell
.\.venv\Scripts\python.exe -u .\src\etl.py --probe-date --date-source A
```

A 使用新浪日线；若其失败，B 使用腾讯日线。更换时先将上述探测命令的 `--date-source A` 改为 `--date-source B`，核对 B 的真实日期、收盘列及最新日期，再按实际列名正式运行。不能因接口失败而猜测列名或把旧行情标成今天。

静态预览（不包含业务后端）：

```powershell
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory public
```

随后打开 `http://127.0.0.1:8765/`。不要直接双击 HTML，浏览器通常会阻止它读取本地 JSON。按 Ctrl+C 停止预览服务。

## 文件结构

```text
market-pulse/
  .github/workflows/update.yml
  public/index.html
  public/history.json
  src/etl.py
  requirements.txt
  README.md
  analysis-draft.md
  failure-drill.md
```

`analysis-draft.md` 是作品集说明草稿，不参与取数或展示。`.venv/`、`work/`、探测日志都不是前端发布内容，不应作为网站文件上传。

## 运行与验证记录

| 时间（北京时间） | 验证 | 结果 |
| --- | --- | --- |
| 2026-10-12 21:19 | 新浪指数、股票接口取数探测 | 成功；指数 562 行，股票 5,572 行；真实字段已核对 |
| 2026-10-12 23:19 | 上证日线日期接口探测 | 成功；最新日线 2026-10-12，字段 `date`、`close` 已核对 |
| 2026-10-12 23:21 | ETL 试算 | 成功，不写历史 |
| 2026-10-12 23:22 | ETL 首次正式运行 | 写入 1 条记录，连续记录 1 个交易日 |
| 2026-10-13 00:23 | GitHub Actions 首次检查 | [成功](https://github.com/LibraData/market-pulse/actions/runs/38276969934)；16:00 前正常跳过，历史内容未变 |
| 阶段 4 | 前端本地检查 | 真实 JSON、双轴图、四项拆解、375px 手机与桌面布局、口径链接已检查 |
| 2026-10-13 06:52 | 人为故障演练 | [CI 按预期失败](https://github.com/LibraData/market-pulse/actions/runs/38319127749)；提交步骤跳过，历史未变 |
| 2026-10-13 06:53 | 恢复正常 ETL | [CI 成功](https://github.com/LibraData/market-pulse/actions/runs/38319240735)；早间正常跳过，脚本恢复、历史未变 |
| 2026-10-13（上线） | 云端更新与 Pages 发布 | [完整工作流成功](https://github.com/LibraData/market-pulse/actions/runs/38396397675)；新增 10 月 13 日记录、连续 2 个交易日，公开 HTML 与 JSON 均返回 HTTP 200，浏览器图表正常 |

代码层面已检查分数计算、历史不足、缺失交易日、同日去重、原文件备份、全零行情拒绝，以及 CI 候选校验。上线工作流已覆盖收盘后真实取数、自动提交和网站部署。Cron 准点性与接口长期稳定性仍需根据后续运行记录观察。

## 发布状态与零成本范围

**网站已上线：** https://LibraData.github.io/market-pulse/ 。GitHub Pages 将 `public/` 发布为网站根目录，前端通过 `./history.json` 读取当前部署的数据。

公开仓库可使用 GitHub Free 的 Pages 静态托管及标准 GitHub 托管 runner 的免费 Actions 运行范围。本项目不需要数据库、付费域名或外部 API 密钥；免费服务仍受平台使用额度和限制约束。[Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

Pages 发布源已设为 GitHub Actions。`update.yml` 先更新与校验历史，再由依赖该作业的 Pages 作业重新检出默认分支最新内容、校验站点载荷并发布。取数作业失败时不部署；正常跳过时可发布现有有效历史。更新与部署衔接在同一次工作流中，避免依赖 `GITHUB_TOKEN` 推送再次触发分支发布构建。[发布源说明](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)

官方参考：[Pages 静态托管](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、[Actions 状态徽章](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge)。

## 数据边界与免责声明

AKShare 接口可能改名、超时、缺行或返回延迟数据。上证日线提供日期，可与指数快照核验；股票接口仅含时分秒，无法逐股证明报价属于当天。CSV/JSON 数值一致、交易日有效，也不代表上游数据绝对正确。

**本项目仅供学习、数据分析与作品集展示，不构成投资建议。** 情绪分描述选定口径下的市场状态，不预测未来收益，不能作为单独的买卖依据。使用者应自行核对数据与方法，并理解历史不足和缺失维度对结论的影响。
