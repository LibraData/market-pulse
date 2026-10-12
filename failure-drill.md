# 故障演练清单：报红、恢复、确认历史未被污染

**本次演练已经完成，正常脚本已恢复。你现在不需要再故意改坏代码。** 以下步骤用于以后自行重复演练。

## 本次真实结果

| 检查 | 结果 |
| --- | --- |
| 演练前历史 | 2026-10-12，1 条记录 |
| 故障注入 | 在 ETL 顶层、取数和写文件之前主动抛出 `RuntimeError` |
| CI 报红 | [失败运行](https://github.com/dingyu001209-pixel/market-pulse/actions/runs/38319127749)，北京时间 2026-10-13 06:52 触发 |
| 报红步骤 | `Fetch and calculate candidate JSON` |
| 后续步骤 | 候选校验和历史提交均为 `skipped` |
| 故障备份 | 失败运行仍成功保存日志和演练前历史 artifact |
| 恢复 | 完整恢复原始 ETL，而不是只绕过异常 |
| CI 恢复 | [成功运行](https://github.com/dingyu001209-pixel/market-pulse/actions/runs/38319240735)，北京时间 2026-10-13 06:53 触发 |
| 恢复后行情处理 | 16:00 前正常 `SKIP`，不生成当天记录 |
| 文件核对 | 报红后、恢复后，历史内容与演练前一致，Git blob SHA 也一致 |

失败日志明确包含：

```text
RuntimeError: DRILL: intentional failure before fetching or writing data
```

演练前后历史文件 SHA：

```text
84ae374d95e90a5d44b18a76f91a99bf5509e355
```

恢复后的 ETL 与演练前内容相同，文件 SHA：

```text
122fb33c4caa666f2724417578a1430b97dbda45
```

SHA 是 Git 对文件内容的标识，不是密码或访问密钥。

## 1. 先保存基线

选择不会与定时更新碰撞的时间，建议交易日北京时间 16:00 前。这让恢复运行走正常跳过路径，便于比较完整历史内容是否完全相同。

打开 PowerShell，先进入项目目录：

```powershell
Set-Location "$env:USERPROFILE\Documents\market-pulse"
```

下列命令从公开仓库读取**云端**历史与脚本，保存到本地 `work/drill/`。不需要 token，不修改仓库，也不拿本地旧历史冒充云端基线。

```powershell
New-Item -ItemType Directory -Force -Path .\work\drill | Out-Null
$drillHistory = Invoke-RestMethod -Uri "https://api.github.com/repos/dingyu001209-pixel/market-pulse/contents/public/history.json?ref=main"
$drillETL = Invoke-RestMethod -Uri "https://api.github.com/repos/dingyu001209-pixel/market-pulse/contents/src/etl.py?ref=main"
[System.IO.File]::WriteAllBytes((Join-Path (Get-Location).Path "work\drill\history-before.json"), [Convert]::FromBase64String($drillHistory.content))
[System.IO.File]::WriteAllBytes((Join-Path (Get-Location).Path "work\drill\etl-before.py"), [Convert]::FromBase64String($drillETL.content))
Set-Content -LiteralPath .\work\drill\history-before-sha.txt -Value $drillHistory.sha -Encoding utf8
$drillHistory.sha
```

如果自行重做，**跑这组命令，然后把输出贴给我**。没有成功保存基线时，先处理报错，不进行故障注入。

## 2. 故意搞坏一次

在 GitHub 打开 [src/etl.py](https://github.com/dingyu001209-pixel/market-pulse/blob/main/src/etl.py)，点击铅笔编辑按钮。

在顶部文档字符串之后、`import argparse` 之前，单独加入下面这一行。必须放在顶层，不要缩进，也不要放在某个取数函数的 `try/except` 里。

```python
raise RuntimeError("DRILL: intentional failure before fetching or writing data")
```

提交到 `main`，提交说明可以写 `test: intentional ETL failure drill`。当前工作流监听 `src/etl.py` 的改动，会自动开始一次运行。

不要用临时断网代替这次错误：网络异常可能被重试和分段容错处理，并不一定报红。这一行错误与接口可用性、交易日或收盘时间无关，能确定地触发程序失败。

## 3. 看 CI 报红

打开仓库的 [Actions](https://github.com/dingyu001209-pixel/market-pulse/actions)，找到这次提交对应的 `Update market pulse`。等待状态结束，再点击运行和 `update` 作业。

- [ ] 作业结论为 `failure`，不是仍在运行的黄色状态。
- [ ] `Fetch and calculate candidate JSON` 显示失败。
- [ ] 展开该步骤，看到上述 `RuntimeError: DRILL`。
- [ ] `Validate candidate and preserve previous dates` 被跳过。
- [ ] `Commit validated history only` 被跳过。
- [ ] `Save logs and history backups` 仍成功；运行页 artifact 可下载，保留 7 天。

红色说明这次工作流失败，不等于历史已经损坏；下一步必须单独核对数据。

## 4. 报红后先核对历史

回到项目目录的 PowerShell：

```powershell
$drillBeforeSHA = (Get-Content -LiteralPath .\work\drill\history-before-sha.txt -Raw).Trim()
$drillAfterSHA = (Invoke-RestMethod -Uri "https://api.github.com/repos/dingyu001209-pixel/market-pulse/contents/public/history.json?ref=main").sha
if ($drillBeforeSHA -eq $drillAfterSHA) { Write-Output "PASS: 云端历史内容未变" } else { Write-Output "CHECK: 云端历史已变化，需要核对差异" }
```

**跑这组命令，然后把输出贴给我。** 应显示 `PASS`。若出现 `CHECK`，先核对是否恰好发生了一次合法的收盘后更新，不要直接用旧备份覆盖新记录。

## 5. 恢复并看 CI 变绿

重新编辑 `src/etl.py`，只删除刚才加入的 `raise RuntimeError(...)` 那一行。检查其余代码没有变化后提交，说明可以写 `fix: restore ETL after failure drill`。

不要在失败的旧提交上只点“重新运行”：只要错误代码仍在，它会再次失败。必须先提交恢复后的代码。

- [ ] 新提交触发新的运行，结论为 `success`。
- [ ] 日志中不再出现人为注入的 `DRILL` 错误。
- [ ] 16:00 前恢复时，日志显示 `SKIP`，并说明历史保持原样。
- [ ] 恢复后再次执行第 4 步的命令，结果仍为 `PASS`。
- [ ] 可与 `work/drill/etl-before.py` 对照，确认正常脚本已完整恢复。

## 6. 演练通过标准与验证范围

通过必须同时满足：**故障运行报红、错误位置明确、历史提交被跳过、正常代码恢复、恢复运行成功、历史内容核对通过。** 不能只看到绿色徽章就宣布通过。

本次已真实验证上述故障路径。恢复发生在收盘前，成功运行对应正常跳过；这不证明当天收盘后接口一定可用，也没有验证真实行情自动提交和网站部署。四项指标的计算与候选校验已有本地检查，仍需观察后续实际定时运行。

如果未来在收盘后重做，合法更新可能改变历史 SHA。此时应逐日期比较：过去记录不被删除或改动、同日最多一条、分数与子分一致，并核对更新日期。不要为了得到相同 SHA 而删除合法数据。
