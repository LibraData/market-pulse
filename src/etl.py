"""market-pulse ETL. Python 3.11 / AKShare 1.19.1 / pandas 2.2.3."""

import argparse
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, time as clock_time, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import akshare as ak
import pandas as pd


CN = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = {"strength": 0.30, "breadth": 0.25, "volume": 0.25, "position": 0.20}
INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
}
LOG = logging.getLogger("market-pulse")


class InvalidMarket(Exception):
    """Expected skip: market date or data validity cannot be established."""


def fetch(label, api_name, **kwargs):
    # Each segment gets its own exception handling and exponential backoff.
    for attempt in range(4):
        try:
            api = getattr(ak, api_name, None)
            if not callable(api):
                raise AttributeError(f"当前版本不存在接口 {api_name}")
            LOG.info("取数 %s：%s，尝试 %s/4", label, api_name, attempt + 1)
            df = api(**kwargs)
            if not isinstance(df, pd.DataFrame):
                raise TypeError("接口未返回 DataFrame")
            print(f"\n===== {label} / {api_name} =====", flush=True)
            print("columns：", flush=True)
            print(df.columns, flush=True)
            print("前两行：", flush=True)
            print(df.head(2).to_string(index=False), flush=True)
            print(f"shape：{df.shape}", flush=True)
            if df.empty or not df.columns.is_unique:
                raise ValueError("空表或重复列名")
            return df
        except Exception as exc:
            LOG.warning("%s：%s: %s", label, type(exc).__name__, exc)
            if attempt < 3:
                delay = 5 * 2**attempt
                LOG.info("%s 秒后重试", delay)
                time.sleep(delay)
    LOG.error("%s 获取失败", label)
    return None


def require_columns(df, columns):
    missing = sorted(set(columns) - set(df.columns))
    if missing:
        raise ValueError(f"缺少已核对字段：{missing}；实际字段：{list(df.columns)}")


def numeric(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("非有限数值")
    return result


def clamp(value):
    return round(max(-100.0, min(100.0, numeric(value))), 4)


def classify(score):
    if score > 60:
        return "过热"
    if score >= 20:
        return "偏暖"
    if score >= -20:
        return "中性"
    if score >= -60:
        return "偏冷"
    return "恐慌"


def trade_calendar(today):
    df = fetch("交易日历", "tool_trade_date_hist_sina")
    if df is None:
        raise RuntimeError("交易日历失败，禁止写入")
    require_columns(df, ["trade_date"])
    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    if dates.isna().any():
        raise RuntimeError("交易日历包含无效日期")
    days = sorted(set(dates.dt.date))
    if today < days[0] or today > days[-1]:
        raise RuntimeError("日历未覆盖今天，禁止用星期几猜测交易日")
    if today not in days:
        raise InvalidMarket("今天不是交易日")
    return days


def date_reference(args, today):
    if args.date_source == "A":
        df = fetch("上证日线日期核验", "stock_zh_index_daily", symbol="sh000001")
    else:
        df = fetch(
            "上证日线日期核验", "stock_zh_index_daily_tx",
            symbol="sh000001", start_date=(today - timedelta(days=90)).strftime("%Y%m%d"),
            end_date=today.strftime("%Y%m%d"),
        )
    if df is None:
        raise RuntimeError("行情日期核验失败，禁止写入")
    print("末两行（原始顺序）：", flush=True)
    print(df.tail(2).to_string(index=False), flush=True)
    if args.probe_date:
        return None
    if not args.date_column or not args.close_column:
        raise RuntimeError("请先 --probe-date 核对真实列名，再指定 --date-column 和 --close-column")
    require_columns(df, [args.date_column, args.close_column])
    clean = pd.DataFrame({
        "day": pd.to_datetime(df[args.date_column], errors="coerce").dt.date,
        "close": pd.to_numeric(df[args.close_column], errors="coerce"),
    })
    if clean.isna().any().any():
        raise InvalidMarket("上证日线含无效日期或价格")
    clean = clean.drop_duplicates("day", keep="last").sort_values("day")
    if len(clean) < 2 or clean.iloc[-1]["day"] != today:
        raise InvalidMarket("上证日线尚未更新到今天，跳过，稍后重跑")
    if (clean["close"] <= 0).any():
        raise InvalidMarket("上证日线价格非正或全零")
    return numeric(clean.iloc[-1]["close"]), numeric(clean.iloc[-2]["close"])


def index_dimension(df, reference):
    if df is None:
        LOG.warning("指数行情失败，丢弃强度；上证收盘使用已核验当日日线")
        return reference[0], {}, None, None
    require_columns(df, ["代码", "最新价", "昨收", "涨跌幅", "成交额"])
    df = df.copy()
    df["代码"] = df["代码"].astype(str).str.lower().str.strip()
    if df["代码"].duplicated().any():
        raise ValueError("指数代码重复")
    df = df.set_index("代码")
    if "sh000001" not in df.index:
        raise ValueError("缺少上证实时行情，丢弃强度")
    anchor = df.loc["sh000001"]
    close, previous_close = numeric(anchor["最新价"]), numeric(anchor["昨收"])
    if close <= 0 or numeric(anchor["成交额"]) <= 0:
        raise InvalidMarket("上证行情价格或成交额为零")
    if abs(close - reference[0]) > 0.03 or abs(previous_close - reference[1]) > 0.03:
        raise InvalidMarket("实时上证价格/昨收与当日日线不一致，可能为旧行情")
    changes = {}
    for code, name in INDEX_CODES.items():
        try:
            row = df.loc[code]
            price, change = numeric(row["最新价"]), numeric(row["涨跌幅"])
            previous = numeric(row["昨收"])
            if price <= 0 or previous <= 0 or numeric(row["成交额"]) <= 0:
                raise ValueError("价格或成交额无效")
            if abs((price / previous - 1) * 100 - change) > 0.03:
                raise ValueError("涨跌幅与最新价/昨收不一致")
            changes[code] = {"name": name, "pct_change": change}
        except Exception as exc:
            LOG.warning("指数 %s 无效：%s", code, exc)
    # The specified four-index median is not replaced by a smaller basket.
    median = float(pd.Series([v["pct_change"] for v in changes.values()]).median()) if len(changes) == 4 else None
    strength = clamp(median * 20) if median is not None else None
    return close, changes, median, strength


def market_dimension(df):
    require_columns(df, ["代码", "最新价", "昨收", "涨跌幅", "成交额", "时间戳"])
    data = df.copy()
    data["代码"] = data["代码"].astype(str).str.lower().str.strip()
    if data["代码"].duplicated().any():
        raise ValueError("股票代码重复")
    if not data["代码"].str.fullmatch(r"(?:sh|sz|bj)\d{6}").all():
        raise ValueError("股票代码格式变化，请重新探测")
    for column in ["最新价", "昨收", "涨跌幅", "成交额"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        if data[column].isna().any() or not data[column].map(math.isfinite).all():
            raise ValueError(f"{column} 有缺失或非有限数值，该维度不出数")
    if (data["成交额"] < 0).any():
        raise ValueError("成交额为负")
    if data["成交额"].sum() <= 0 or (data["最新价"] <= 0).all():
        raise InvalidMarket("股票市场为全零/无成交行情")
    # Zero-turnover rows (including suspended stocks) are excluded from breadth.
    active = data[data["成交额"] > 0].copy()
    if (active[["最新价", "昨收"]] <= 0).any().any():
        raise ValueError("有成交但价格非正")
    calculated = (active["最新价"] / active["昨收"] - 1) * 100
    if ((calculated - active["涨跌幅"]).abs() > 0.03).any():
        raise ValueError("股票涨跌幅与最新价/昨收不一致")
    quote_times = pd.to_datetime(active["时间戳"].astype(str), format="%H:%M:%S", errors="coerce")
    if quote_times.isna().any():
        raise ValueError("时间戳格式变化，请重新核对")
    if quote_times.dt.hour.max() < 15:
        raise ValueError("尚未出现收盘时间的股票报价")
    up = int((active["涨跌幅"] > 0).sum())
    down = int((active["涨跌幅"] < 0).sum())
    flat = int((active["涨跌幅"] == 0).sum())
    breadth = clamp((up / (up + down) - 0.5) * 200) if up + down else None
    return {
        "up": up, "down": down, "flat": flat,
        "universe_count": len(data), "active_count": len(active),
        "turnover_yuan": numeric(data["成交额"].sum()),
        "breadth_score": breadth,
    }


def read_history(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("历史格式不兼容，禁止覆盖；新项目请不要手工创建 history.json")
    rows = document.get("records")
    if not isinstance(rows, list):
        raise ValueError("历史 records 不是列表")
    unique = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("历史记录不是对象")
        day = date.fromisoformat(row["date"]).isoformat()
        if day != row["date"]:
            raise ValueError("历史日期必须为 YYYY-MM-DD")
        numeric(row["score"])
        price = numeric(row["sh_close"])
        if price <= 0:
            raise ValueError("历史上证价格无效")
        unique[day] = row  # File order determines the last saved version.
    return [unique[day] for day in sorted(unique)]


def trailing_rows(history, days, today):
    previous_days = [d.isoformat() for d in days if d < today][-20:]
    by_day = {row["date"]: row for row in history}
    if len(previous_days) != 20 or any(day not in by_day for day in previous_days):
        return []
    return [by_day[day] for day in previous_days]


def make_record(today, now, history, days, close, changes, median, strength, market, reasons, date_source):
    scores = {"strength": strength, "breadth": None, "volume": None, "position": 0.0}
    scores["breadth"] = market["breadth_score"] if market else None
    prior = trailing_rows(history, days, today)
    amount_mean, ratio, ma20 = None, None, None
    if len(prior) == 20:
        # MA20 includes today's close and the preceding 19 trading closes.
        ma20 = (sum(numeric(row["sh_close"]) for row in prior[-19:]) + close) / 20
        scores["position"] = clamp((close / ma20 - 1) * 100 * 5)
    else:
        reasons["position"] = "前20个交易日的本地记录不足，按约定为0"
    if market and median is not None and len(prior) == 20:
        try:
            amounts = [numeric(row["turnover_yuan"]) for row in prior]
            if any(value <= 0 for value in amounts):
                raise ValueError("过去成交额非正")
            amount_mean = sum(amounts) / 20
            ratio = market["turnover_yuan"] / amount_mean
            direction = 1 if median > 0 else -1 if median < 0 else 0
            scores["volume"] = clamp(direction * ratio * 50)
        except Exception as exc:
            reasons["volume"] = f"过去20日成交额不可用：{exc}"
    else:
        reasons["volume"] = "缺少当日成交额、四指数方向或前20个交易日完整基准"
    if strength is None:
        reasons["strength"] = "四指数涨跌幅不齐，禁止用更小的指数篮子替代"
    if scores["breadth"] is None:
        reasons.setdefault("breadth", "上涨加下跌家数为0或股票维度不可用")
    available = [key for key, value in scores.items() if value is not None]
    if not any(key in available for key in ("strength", "breadth", "volume")):
        raise InvalidMarket("没有可用的当日情绪维度")
    score = round(sum(WEIGHTS[key] * scores[key] for key in available), 4)
    return {
        "date": today.isoformat(), "updated_at": now.isoformat(timespec="seconds"),
        "score": score, "level": classify(score), "subscores": scores,
        "score_complete": len(available) == 4,
        "available_weight": round(sum(WEIGHTS[key] for key in available), 2),
        "missing_reasons": reasons, "sh_close": close,
        "index_changes": changes, "index_median_pct": median,
        "up": market["up"] if market else None,
        "down": market["down"] if market else None,
        "flat": market["flat"] if market else None,
        "universe_count": market["universe_count"] if market else None,
        "active_count": market["active_count"] if market else None,
        "turnover_yuan": market["turnover_yuan"] if market else None,
        "turnover_mean20_yuan": amount_mean, "turnover_ratio20": ratio,
        "sh_ma20": ma20,
        "sources": {"spot": "sina", "date_reference": date_source},
        "freshness": {
            "index_date_verified": True,
            "stock_date_verified": False,
            "note": "股票接口仅含时分秒；使用当日日线和上证报价交叉核验，不能逐股证明报价日期",
        },
        "methodology_version": "1.0",
    }


def write_history(path, history, record, days):
    by_day = {row["date"]: row for row in history}
    by_day[record["date"]] = record
    rows = [by_day[day] for day in sorted(by_day)]
    recorded = set(by_day)
    streak = 0
    latest = date.fromisoformat(rows[-1]["date"])
    for day in reversed([day for day in days if day <= latest]):
        if day.isoformat() not in recorded:
            break
        streak += 1
    document = {
        "schema_version": 1,
        "metadata": {
            "data_date": rows[-1]["date"], "updated_at": record["updated_at"],
            "record_count": len(rows), "consecutive_trading_days": streak,
            "weights": WEIGHTS,
            "score_policy": "缺失子分为null，保留固定权重，不重新归一化；位置历史不足为0",
            "methodology_url": "index.html#methodology",
            "disclaimer": "仅供数据分析展示，不构成投资建议。数据可能延迟或有误。",
        },
        "records": rows,
    }
    serialized = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_dir = ROOT / "work" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S%f")
        backup = backup_dir / f"history-{stamp}-{uuid.uuid4().hex[:8]}.json"
        shutil.copy2(path, backup)
        LOG.info("已备份旧文件：%s", backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".history-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    LOG.info("写入成功：%s；记录 %s 条；连续 %s 个交易日", path, len(rows), streak)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="market-pulse ETL")
    parser.add_argument("--probe-date", action="store_true", help="只探测日线列名，不写入JSON")
    parser.add_argument("--date-source", choices=("A", "B"), default="A", help="A新浪日线；B腾讯日线")
    parser.add_argument("--date-column", default=os.environ.get("ETL_DATE_COLUMN"))
    parser.add_argument("--close-column", default=os.environ.get("ETL_CLOSE_COLUMN"))
    parser.add_argument("--dry-run", action="store_true", help="核验并计算，但不写入JSON")
    args = parser.parse_args()
    LOG.info("Python=%s AKShare=%s pandas=%s", sys.version.split()[0], version("akshare"), pd.__version__)
    if sys.version_info[:2] != (3, 11) or version("akshare") != "1.19.1" or pd.__version__ != "2.2.3":
        LOG.error("版本不匹配：要求 Python 3.11 / AKShare 1.19.1 / pandas 2.2.3")
        return 1
    now = datetime.now(CN)
    today = now.date()
    lock_path = ROOT / "work" / "etl.lock"
    locked = False
    try:
        days = trade_calendar(today)
        if now.time() < clock_time(16, 0):
            raise InvalidMarket("北京时间未到16:00，不采集收盘行情")
        reference = date_reference(args, today)
        if args.probe_date:
            LOG.info("探测结束，请回传列名与末两行；未写JSON")
            return 0
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("x", encoding="utf-8") as handle:
            locked = True
            handle.write(str(os.getpid()))
        path = ROOT / "public" / "history.json"
        history = read_history(path)
        if history and history[-1]["date"] > today.isoformat():
            raise RuntimeError("历史包含未来日期，禁止写入")
        index_df = fetch("指数行情", "stock_zh_index_spot_sina")
        try:
            close, changes, median, strength = index_dimension(index_df, reference)
        except InvalidMarket:
            raise
        except Exception as exc:
            LOG.warning("指数维度丢弃：%s；上证收盘使用已核验当日日线", exc)
            close, changes, median, strength = reference[0], {}, None, None
        market_df = fetch("市场广度原始明细", "stock_zh_a_spot")
        market, reasons = None, {}
        if market_df is None:
            reasons["breadth"] = "股票接口重试后仍失败，广度和成交额不可用"
        else:
            try:
                market = market_dimension(market_df)
            except InvalidMarket:
                raise
            except Exception as exc:
                LOG.warning("股票维度丢弃：%s", exc)
                reasons["breadth"] = str(exc)
        if market and history:
            previous_count = history[-1].get("universe_count")
            if previous_count and market["universe_count"] < previous_count * 0.95:
                reasons["breadth"] = "股票数量较最近记录减少超过5%，疑似分页不完整"
                LOG.warning(reasons["breadth"])
                market = None
        record = make_record(today, now, history, days, close, changes, median,
                             strength, market, reasons, args.date_source)
        print("\n当日记录：", flush=True)
        print(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
        if args.dry_run:
            LOG.info("dry-run 完成，未写JSON")
        else:
            write_history(path, history, record, days)
        return 0
    except InvalidMarket as exc:
        LOG.warning("SKIP：%s；history.json 保持原样", exc)
        return 0
    except Exception:
        LOG.exception("FAIL：运行失败，未完成历史写入")
        return 1
    finally:
        if locked:
            lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
