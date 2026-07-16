# A股选股系统

个人 A 股选股研究系统。后端使用 FastAPI 和 MySQL，研究层实现真实行情因子、滚动模型和样本外回测，前端为浅色静态仪表盘。

## 当前能力

- MySQL schema 和 SQLAlchemy ORM。
- Tushare Pro 沪深市场按交易日增量同步，不包含北交所。
- 原始行情、复权因子、每日指标和稳定后复权研究价分别存储。
- 三类日数据并发请求、6000 行分页、驱动级批量 upsert、日期级完整性校验、失败停止和独立同步断点。
- 数据管理页面：全量/断点续传、启动/停止、进度、日志和 MySQL 存量；Token 仅从 `.env` 读取。
- 8 个价格/成交量因子的批量计算、去极值和每日截面标准化。
- 5/20/60 日逻辑回归滚动训练及样本外上涨概率。
- 面向 1–10 万元资金的动态 3–10 只持仓、100 股整手分配和无最低佣金回测。
- 非重叠滚动回测、佣金、卖出印花税、滑点、换手、Rank IC、Sharpe 和最大回撤。
- Demo 模式下可直接查看推荐列表、个股解释和回测摘要。
- API:
  - `GET /api/health`
  - `GET /api/recommendations`
  - `GET /api/stocks/{code}/explain`
  - `GET /api/backtest/summary`
  - `GET /api/data/config`
  - `GET /api/data/inventory`
  - `POST /api/data/sync`
  - `POST /api/data/sync/stop`
  - `GET /api/data/sync/status`

## 启动

### Windows 一键启动

双击项目根目录的 `start.bat`。脚本会优先使用 `.venv`，直接启动服务并打开浏览器：

```text
http://127.0.0.1:8000
```

使用期间不要关闭启动窗口；按 `Ctrl+C` 可停止服务。数据库初始化只需首次执行，行情同步和研究计算可在需要更新数据时执行，不必随 Web 服务重复启动。

### 手动启动

安装依赖：

```powershell
pip install -r requirements.txt
```

启动 demo 模式：

```powershell
$env:APP_DEMO_MODE="true"
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

```text
http://127.0.0.1:8000
```

## MySQL 与真实数据

项目根目录 `.env` 已配置本机 MySQL。首次执行自动建库建表：

```powershell
python scripts/init_mysql.py
```

Tushare Pro Token 只从项目根目录 `.env` 读取，前端不提供查看或修改入口：

```text
TUSHARE_TOKEN=你的_token
```

只读检查 Token、接口权限、分页和日期数据完整性：

```powershell
python scripts/check_tushare.py --trade-date 20240102
```

再用一个交易日验证 MySQL 写入链路：

```powershell
python scripts/sync_tushare.py --start-date 20240102 --end-date 20240102 --max-dates 1
```

通过页面运行时无需指定日期。首次启动新的全量任务会从 `2018-01-01` 拉取沪深市场至当天（不包含北交所），并在每个成功交易日后保存连续断点；完成、失败、主动停止或进程意外退出后，下次点击“开始 / 继续拉取”都会从最后成功日期的下一天继续。旧版从 2025 年开始的断点不会跳过这次首次历史回补。

命令行执行相同范围：

```powershell
python scripts/sync_tushare.py --start-date 20180101
```

以后增量更新无需指定日期，脚本会从已保存断点的下一天继续：

```powershell
python scripts/sync_tushare.py
```

仅拉指定代码时仍按交易日请求后过滤，应明确给出开始日期：

```powershell
python scripts/sync_tushare.py --codes 600519,000858 --start-date 20240101
```

同步默认在首个失败日期停止，修复权限或网络问题后重新运行即可从安全断点恢复。只有明确接受中间缺口时才使用 `--continue-on-error`。

拉取完成后执行因子、训练和回测：

```powershell
python scripts/run_research.py --start-date 20180101 --horizons 5,20,60 --initial-capital 50000
```

分析页可输入 `10000` 至 `100000` 元可用资金。系统会按资金规模动态选择 3–10 只股票，为每只股票保留可买入 100 股整数手的目标金额，并预留 3% 现金。研究回测默认本金 5 万元、无最低佣金，比例佣金、卖出印花税和双边滑点均可通过命令行参数调整。

启动页面。数据拉取期间使用单 worker，并仅监听本机地址：

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

后台任务状态保存在当前 Web 进程内；进程中断后数据库断点仍保留，重新启动后点击“开始 / 继续拉取”即可安全续传。服务不提供 Token 的查看或写入接口。

## 文档

完整开发文档和算法口径见 `docs/DEVELOPMENT.md`。
