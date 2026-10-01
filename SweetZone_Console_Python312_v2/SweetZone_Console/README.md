# 甜蜜区本地筛选器 2.0

Windows / PyCharm / Python 3.12。仅维护沪深主板当前非ST股票。按交易日批量下载两年日K和辅助数据，统一筛选口径。服务端出现任何接口频次超限时，保存已完成工作并停止，不重试、不继续下一只股票。

## 最快开始

1. 将本压缩包解压到一个新文件夹，用 PyCharm 打开其中的 `SweetZone_Console` 文件夹。
2. 在 PyCharm 设置里选择你已经配置好的 Python 3.12.10 解释器。
3. 打开 PyCharm 下方的 **Terminal / 终端**，在项目根目录运行：

   `python -m pip install -r requirements.txt`

   这是终端命令，不要粘贴到带 `>>>` 提示符的 Python Console。
4. 将旧项目的 `.env` 复制到本项目中，与 `main.py` 放在同一个文件夹。如果没有旧文件，复制 `.env.example` 为 `.env`，填写 `TUSHARE_TOKEN=你的密钥`。Windows需显示文件扩展名，避免存成 `.env.txt`。
5. 在 PyCharm 中右键 `main.py`，选择 **Run**。输入菜单编号，首次输入保存数据的文件夹路径；回车使用项目内 `workspace`。

项目不包含你的密钥或旧行情文件。旧项目可继续保留。新程序会记住保存路径；每次启动自动打开本地数据库，不需要上传文件。

## 菜单

| 编号 | 功能 |
| --- | --- |
| 1 | 更新并筛选：完成数据下载后开始本地筛选 |
| 2 | 仅更新：补齐数据，不做筛选 |
| 3 | 仅本地筛选：不读取密钥、不调用任何接口 |
| 4 | 清理旧数据：删除两年加45天缓冲之外的数据 |
| 0 | 退出 |

也可在终端直接运行：

```text
python main.py --mode update-and-screen
python main.py --mode update-only
python main.py --mode screen-only
python main.py --mode prune
python main.py --change-dir
python main.py --mode update-only --data-dir "D:\StockData"
```

可选 `--asof 20260929` 指定目标日期。目标日默认取北京时间18点后最近的交易日；18点前使用上一个交易日。若数据未入库或不完整，程序会停下，保留待补批次。指定过去日期仍使用当前股票名单，只用于本地复查，不是无幸存者偏差的历史回测。

## 旧CSV如何使用

可选导入原项目 `data/daily` 中的未复权CSV：

```text
python main.py --mode update-and-screen --legacy-dir "D:\旧项目\data\daily"
```

导入后不删除来源文件，不覆盖数据库中已存在的数据。旧CSV只有日K，缺少本版所需的复权因子、历史换手率、实际涨停价和批次完整性记录，因此第一次仍会按日期联网校验并补齐。不要把旧版CSV当作已完成的新版本下载。后续增量更新只补未完成批次或新日期；当股票池变化时，相关日期可能需要重新确认覆盖范围。

## 限频与故障

- 正常请求之间至少间隔1.3秒；交易日历请求至少间隔61秒。请求时钟跨启动保存。
- 主动限速时可能短暂等待，这是发请求之前的正常节流。
- **服务端一旦返回频次超限，立即停止整个任务。没有等待后重试。**
- 无权限、网络失败、异常空表、重复分页等错误同样停止，便于定位；下一次由你手动启动。
- 同一数据目录只能运行一个进程。不同目录、其他软件仍可能共用同一账号额度。
- Ctrl+C 会保存当前已产生的筛选结果后退出；强制关窗口时数据库已提交批次仍在，筛选逐股原始记录保存在JSONL。
- 全部下载完成前不会开始本次筛选。如果下载中途停止，本次筛选表为空，并明确标注任务未完成；以前运行的结果保存在各自目录中。

首次下载需要覆盖两年所有日期，并读取四类数据；控制台显示日期批次、完成比例、保存条数和动态预计时间。实际耗时由账号限额、分页数和网络决定。请以控制台进度为准。

## 需要的接口

`stock_basic`、`trade_cal`、`daily`、`adj_factor`、`daily_basic`、`stk_limit`。
除日K外，实际涨停价、复权因子等有独立权限要求。只有daily权限时无法完整执行本版算法；程序不会用9.5%近似涨停或未复权价格替代。本版先处理最新日的四类数据，尽早发现权限问题。

## 保存内容

在选择的数据目录中：

- `market.db`：SQLite数据库，存放日K、复权因子、换手率、市值、涨跌停价、股票名单、交易日历及已完成批次。运行时可能同时出现 `market.db-wal` 和 `market.db-shm`，备份请先关闭程序再复制整个目录。
- `request_clock.json`：请求时钟，用于主动限速。
- `last_run.json`：最近一次运行状态和停止位置。
- `output/运行时间_编号/`：每次运行的独立结果目录，含筛选通过CSV、全部检查CSV、数据问题CSV、任务停止记录CSV、Excel、运行说明JSON和逐股检查点JSONL。

CSV采用UTF-8带BOM编码，Excel/WPS可直接打开。Excel的“运行说明”页标明任务完整性。`completed_with_data_issues` 表示扫描完成但有股票资料不足；`stopped` 或 `interrupted` 表示本次未完成。筛选结果是候选名单，不执行自动交易。

当前停牌或数据不新鲜的股票采用其最近有效日K对应的价格和市值，并显示最新日期及距目标日天数。离线模式使用缓存名单，名称和ST状态截至名单更新日。当前变为ST或退市的股票不再维护，原记录保留，清理旧数据时按时间统一清理。

## 算法与参数

完整定义见 `docs/甜蜜区筛选器需求说明书.docx` 和 `docs/需求说明.md`。所有阈值见 `config.json`。复权计算使用原价乘当日因子再除以最新有效日因子，涨停判断使用未复权实际价格；不得将两者混用。

运行窗口按每只股票实际有日K的交易日计数，停牌不补造K线。底部巨量的历史低点按形态发生日向前滚动一年计算。分支缺数据时标为未知；只要基础条件满足且另一个分支明确命中，仍可通过，并展示数据说明。没有明确命中且存在未知分支时归入“数据不足”，不作“不符合形态”处理。

## 离线验证

```text
python -m unittest discover -s tests -v
```

测试使用模拟数据与模拟接口，不消耗Tushare额度。交付已完成离线测试与语法检查；尚未用你的账号做真实接口联调，也未在你的Windows/PyCharm环境直接执行。若报错，请提供控制台停止信息或 `last_run.json`，不需要发送Token。

## 官方接口依据

- 股票名单：https://tushare.pro/document/2?doc_id=25
- 交易日历：https://tushare.pro/document/2?doc_id=26
- 日K与按日期批量调用：https://tushare.pro/document/2?doc_id=27
- 复权因子：https://tushare.pro/document/2?doc_id=28
- 每日指标：https://tushare.pro/document/2?doc_id=32
- 实际涨跌停价：https://tushare.pro/document/2?doc_id=183
- 权限与频次：https://tushare.pro/document/1?doc_id=108
