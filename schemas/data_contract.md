# 原始数据契约（Data Contract）

流水线只需维护以下 2 个原始 JSON 文件，然后执行 `python pipeline/build_snapshot.py` 即可生成前端所需的 `frontend/data/snapshot.json`。

## 1. `data/views.json`（卖方观点库）

JSON 数组，每条元素对应一份研报中的一个期限观点。

```json
[
  {
    "broker": "招商证券",
    "series": "利率策略周报",
    "report_date": "2026-11-22",
    "week_mon": "2026-11-23",
    "pdf_url": "https://drive.google.com/file/d/xxxx/view",
    "tenor": "10Y国债",
    "raw_tenor": "10年国债",
    "direction": 0,
    "action": "区间波段",
    "target_type": "明确区间",
    "target_low": 1.60,
    "target_high": 1.70,
    "target_note": "1.60%-1.70%",
    "horizon": "短期(1-2周)",
    "drivers": [
      { "layer": "资金面", "impact": "利多", "phrase": "低融资成本托底" },
      { "layer": "估值情绪", "impact": "利空", "phrase": "股债跷跷板扰动" }
    ],
    "risk": "跨月资金波动超预期",
    "logic": "低融资成本和供给缓和托底，但股债跷跷板制约下行空间，10Y维持1.60%-1.70%区间波段。",
    "cond": "若回升至1.70%上方可逢调加仓",
    "quote": "短期预计10年国债在1.6%—1.7%区间震荡，不激进追涨、等待扰动释放。",
    "page": 2
  }
]
```

- **去重主键**：`(broker, report_date, tenor)`。同一份研报重跑时按主键覆盖更新。
- **`week_mon`（可选）**：该研报归属的观察周周一（`YYYY-MM-DD`）。若省略，`build_snapshot.py` 会自动计算（周日发布的周报归入次日周一）。
- **`pdf_url`（可选）**：上传 Google Drive 后拿到的查看链接；前端弹窗会自动渲染「打开原 PDF ↗」。

## 2. `data/yields.json`（日度国债收益率，可选但强烈推荐）

JSON 数组，按交易日记录关键国债收益率（%）及 30Y-10Y 利差（bp）。**即使暂时没接行情数据（空数组 `[]`），构建脚本和前端也能正常运行，只是曲线走势与顺势程度会自动隐藏。**

```json
[
  {
    "date": "2026-11-20",
    "1Y": 1.15,
    "3Y": 1.32,
    "5Y": 1.45,
    "7Y": 1.56,
    "10Y": 1.65,
    "30Y": 1.95,
    "30-10": 30.0
  }
]
```

- `build_snapshot.py` 会自动将 `短端(1-3Y)` 映射到 `1Y`、`中端(5-7Y)` 映射到 `5Y`、`10Y国债` 映射到 `10Y`、`30Y超长债` 映射到 `30Y`、`曲线形态` 映射到 `30-10`（bp）。
