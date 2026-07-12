# A股选股系统

个人 A 股选股研究系统。后端使用 FastAPI 和 MySQL，研究层实现真实行情因子、滚动模型和样本外回测，前端为浅色静态仪表盘。

## 当前能力

- MySQL schema 和 SQLAlchemy ORM。
- Tushare Pro 全市场按交易日增量同步。
- 原始行情、复权因子、每日指标和稳定后复权研究价分别存储。
- 5000 行分页、日期级完整性校验、失败停止和独立同步断点。
- 数据管理页面：Token、日期范围、Sleep、重试、启动/停止、进度、日志和 MySQL 存量。
- 8 个价格/成交量因子的批量计算、去极值和每日截面标准化。
- 5/20/60 日逻辑回归滚动训练及样本外上涨概率。
- Top N 非重叠滚动回测、交易成本、换手、Rank IC、Sharpe 和最大回撤。
- Demo 模式下可直接查看推荐列表、个股解释和回测摘要。
- API:
  - `GET /api/health`
  - `GET /api/recommendations`
  - `GET /api/stocks/{code}/explain`
  - `GET /api/backtest/summary`
  - `GET /api/data/config`
  - `PUT /api/data/token`
  - `GET /api/data/inventory`
  - `POST /api/data/sync`
  - `POST /api/data/sync/stop`
  - `GET /api/data/sync/status`

## 启动

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

Tushare Pro Token 从项目根目录 `.env` 读取：

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

通过页面运行时，“数据管理”默认拉取 `2026-01-01` 至当天，默认 Sleep 为 `0.8` 秒，并从安全断点续传。关闭断点选项可强制重拉整个日期区间。

命令行执行相同范围：

```powershell
python scripts/sync_tushare.py --start-date 20260101 --sleep 0.8
```

以后增量更新无需指定日期，脚本会向前刷新最近 7 个自然日：

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
python scripts/run_research.py --start-date 20180101 --horizons 5,20,60 --top-n 20
```

启动页面。数据拉取期间使用单 worker，并仅监听本机地址：

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

后台任务状态保存在当前 Web 进程内；进程中断后数据库断点仍保留，重新启动后再次提交相同日期范围即可安全续传。不要把包含 Token 管理接口的服务暴露到公网。

## 文档

完整开发文档和算法口径见 `docs/DEVELOPMENT.md`。
