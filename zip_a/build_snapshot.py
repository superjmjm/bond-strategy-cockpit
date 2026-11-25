#!/usr/bin/env python3
"""
Zero-dependency static snapshot builder for the Bond Strategy Dashboard.

Usage:
  # 1) Generate demo raw data & build frontend/data/snapshot.json:
  python pipeline/build_snapshot.py --seed-demo

  # 2) In GitHub Actions (after updating data/views.json & data/yields.json):
  python pipeline/build_snapshot.py
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_PATH = ROOT / "frontend" / "data" / "snapshot.json"

# 5 个实战期限桶（前端展示顺序）
TENORS: list[dict[str, Any]] = [
    {"id": "短端(1-3Y)", "short": "短端", "type": "yield", "ykey": "2Y", "sc": 6.0},
    {"id": "中端(5-7Y)", "short": "中端", "type": "yield", "ykey": "7Y", "sc": 7.0},
    {"id": "10Y国债", "short": "10Y", "type": "yield", "ykey": "10Y", "sc": 8.0},
    {"id": "30Y超长债", "short": "30Y", "type": "yield", "ykey": "30Y", "sc": 10.0},
    {"id": "曲线形态", "short": "曲线", "type": "spread", "ykey": "30-10", "sc": 4.0},
]
TENORS.insert(0, {"id":"整体久期/组合","short":"组合","type":"portfolio","ykey":None,"sc":1})
TENOR_IDS = [t["id"] for t in TENORS]
TENOR_MAP = {t["id"]: t for t in TENORS}
YIELD_TENORS = [t for t in TENORS if t["type"] == "yield"]

LAYERS = ["基本面", "政策面", "资金面", "机构行为", "供给面", "估值情绪"]
IMPACTS = {"利多": 1, "中性": 0, "利空": -1}

MIN_SAMPLE_STATS = 4    # 分歧度 / 顺势程度 最小样本数
MIN_SAMPLE_TARGET = 3   # 目标中位 最小给出点位的家数


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _mode(vals: list[str]) -> str:
    return Counter(vals).most_common(1)[0][0] if vals else "—"


def infer_week_mon(report_date: str) -> str:
    d = date.fromisoformat(report_date)
    # 周日发布的周报归入次日周一观察周
    if d.weekday() == 6:
        return (d + timedelta(days=1)).isoformat()
    return (d - timedelta(days=d.weekday())).isoformat()


def iso_week_meta(mon_str: str, ref_idx: int) -> dict[str, Any]:
    d = date.fromisoformat(mon_str)
    iso_year, iso_week, _ = d.isocalendar()
    sun = d + timedelta(days=6)
    return {
        "mon": mon_str,
        "ref_idx": ref_idx,
        "title": f"{iso_year}年第{iso_week}周",
        "range": f"{d.strftime('%m/%d')}–{sun.strftime('%m/%d')}",
        "short": d.strftime("%m/%d"),
    }


def validate_and_dedup_views(raw_views: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """校验并按 (broker, report_date, tenor) 去重（后写入覆盖先写入）。"""
    errors: list[str] = []
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}

    for idx, v in enumerate(raw_views):
        try:
            broker = str(v["broker"]).strip()
            rd = str(v["report_date"]).strip()
            tenor = str(v["tenor"]).strip()
            direction = int(v["direction"])
            if not broker or tenor not in TENOR_MAP or direction not in (-2, -1, 0, 1, 2):
                raise ValueError(f"invalid broker/tenor/direction: {broker}/{tenor}/{direction}")
            date.fromisoformat(rd)
            week_mon = v.get("week_mon") or infer_week_mon(rd)
            date.fromisoformat(week_mon)

            t_low = v.get("target_low")
            t_high = v.get("target_high")
            t_type = v.get("target_type") or ("明确区间" if (t_low is not None and t_high is not None) else "无点位")
            if t_low is not None and t_high is not None:
                t_low, t_high = float(t_low), float(t_high)
                if t_high < t_low:
                    t_low, t_high = t_high, t_low
            else:
                t_low, t_high = None, None

            raw_drivers = v.get("drivers") or []
            drivers = []
            for d in raw_drivers:
                if isinstance(d, dict) and d.get("layer") in LAYERS and d.get("phrase"):
                    imp = d.get("impact") if d.get("impact") in IMPACTS else "中性"
                    drivers.append({"layer": d["layer"], "impact": imp, "phrase": str(d["phrase"]).strip()})

            clean = {
                "broker": broker,
                "series": str(v.get("series") or "债券策略周报").strip(),
                "report_date": rd,
                "week_mon": week_mon,
                "pdf_url": str(v.get("pdf_url") or "").strip(),
                "tenor": tenor,
                "raw_tenor": str(v.get("raw_tenor") or tenor).strip(),
                "direction": direction,
                "action": str(v.get("action") or "持有观望").strip(),
                "target_type": t_type,
                "target_low": t_low,
                "target_high": t_high,
                "target_note": str(v.get("target_note") or "").strip(),
                "horizon": str(v.get("horizon") or "未明确").strip(),
                "drivers": drivers,
                "risk": str(v.get("risk") or "").strip(),
                "logic": str(v.get("logic") or "").strip(),
                "cond": str(v.get("cond") or "暂无明确触发条件").strip(),
                "quote": str(v.get("quote") or "").strip(),
                "page": int(v.get("page") or 1),
            }
            if type(v.get("vote_eligible")) is not bool:
                raise ValueError("Missing vote eligibility")
            clean.update(report_id=v["report_id"],vote_eligible=v["vote_eligible"],evidence=v["evidence"],account_note=v["account_note"])
            dedup[(v["report_id"], tenor)] = clean
        except Exception as exc:
            errors.append(f"Row {idx}: {exc}")

    rows = sorted(dedup.values(), key=lambda x: (x["week_mon"], x["report_date"], x["broker"], TENOR_IDS.index(x["tenor"])))
    for i, r in enumerate(rows, 1):
        r["id"] = i
    return rows, errors


def build_snapshot(raw_views: list[dict[str, Any]], raw_yields: list[dict[str, Any]], reports=()) -> dict[str, Any]:
    views, errors = validate_and_dedup_views(raw_views)
    if errors:
        raise ValueError(f"Invalid card(s): {errors[:3]}")

    # 1) 处理日度收益率（允许为空）
    raw_yields_sorted = sorted(raw_yields, key=lambda r: r["date"])
    dates = [r["date"] for r in raw_yields_sorted]
    has_yields = len(dates) > 0
    yields_by_key: dict[str, list[float | None]] = {}
    for key in ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "30Y", "30-10"]:
        arr = []
        last_v = None
        for r in raw_yields_sorted:
            if r.get(key) is not None:
                last_v = float(r[key])
            elif key == "30-10" and r.get("30Y") is not None and r.get("10Y") is not None:
                last_v = round((float(r["30Y"]) - float(r["10Y"])) * 100.0, 2)
            arr.append(last_v)
        yields_by_key[key] = arr

    # 2) 周索引与券商清单
    week_mons = sorted({v["week_mon"] for v in views} | {infer_week_mon(r["report_date"]) for r in reports})
    weeks: list[dict[str, Any]] = []
    week_idx_map: dict[str, int] = {}
    for wi, mon in enumerate(week_mons):
        ref = -1
        if has_yields:
            for i, td in enumerate(dates):
                if td <= (date.fromisoformat(mon)+timedelta(days=6)).isoformat():
                    ref = i
                else:
                    break
        meta = iso_week_meta(mon, ref)
        meta["idx"] = wi
        weeks.append(meta)
        week_idx_map[mon] = wi

    brokers_map = {r["broker"]:r["series"] for r in reports}
    for v in views:
        brokers_map[v["broker"]] = v["series"]
    brokers = [{"name": k, "series": v} for k, v in brokers_map.items()]
    broker_idx = {b["name"]: i for i, b in enumerate(brokers)}

    # 3) 补充 week_idx、mid、滚动4周 delta_vs_prev
    by_week: list[list[dict[str, Any]]] = [[] for _ in weeks]
    last_seen: dict[tuple[str, str], tuple[int, int]] = {}  # (broker, tenor) -> (week_idx, dir)

    for v in views:
        wi = week_idx_map[v["week_mon"]]
        v["week_idx"] = wi
        if v["target_low"] is not None and v["target_high"] is not None:
            v["mid"] = round((v["target_low"] + v["target_high"]) / 2.0, 4)
        else:
            v["mid"] = None

        key = (v["broker"], v["tenor"])
        prev = last_seen.get(key)
        gap = ((date.fromisoformat(v["week_mon"])-date.fromisoformat(weeks[prev[0]]["mon"])).days//7) if prev is not None else None
        has_prev = gap is not None and 1 <= gap <= 4
        v["prev_dir"] = prev[1] if has_prev else None
        v["gap_weeks"] = gap if has_prev else None
        if not has_prev:
            v["delta"] = "新覆盖"
        elif v["direction"] > prev[1]:
            v["delta"] = "更偏多"
        elif v["direction"] < prev[1]:
            v["delta"] = "更偏空"
        else:
            v["delta"] = "维持"
        if v["vote_eligible"]:
            last_seen[key] = (wi, v["direction"])
        else:
            v["delta"] = "仅展示"

        # 记录发布时参考收益率
        t = TENOR_MAP[v["tenor"]]
        ref_idx = weeks[wi]["ref_idx"]
        v["ref_val"] = yields_by_key[t["ykey"]][ref_idx] if ref_idx >= 0 and t["ykey"] else None
        by_week[wi].append(v)

    # 4) 预计算每个 (week, tenor) 的核心指标
    mc: dict[tuple[int, str], dict[str, Any]] = {}

    def metrics(wi: int, tid: str) -> dict[str, Any]:
        if (wi, tid) in mc:
            return mc[(wi, tid)]
        t = TENOR_MAP[tid]
        vs = [v for v in by_week[wi] if v["tenor"] == tid]
        vs = list({v["broker"]:v for v in vs if v["vote_eligible"]}.values())
        dirs = [v["direction"] for v in vs]
        n = len(vs)
        cons = _mean(dirs)
        disp = _std(dirs) if n >= MIN_SAMPLE_STATS else None
        bull = sum(1 for d in dirs if d > 0)
        bear = sum(1 for d in dirs if d < 0)
        neu = n - bull - bear

        mids = [v["mid"] for v in vs if v["mid"] is not None]
        n_tgt = len(mids)
        med = _median(mids) if n_tgt >= MIN_SAMPLE_TARGET else None

        mult = 1.0 if t["type"] == "spread" else 100.0
        cur = wchg = mom4 = space = trend_align = None
        if weeks[wi]["ref_idx"] >= 0 and t["ykey"]:
            y = yields_by_key[t["ykey"]]
            ref = weeks[wi]["ref_idx"]
            prev_ref = weeks[max(0, wi - 1)]["ref_idx"]
            cur = y[ref]
            if wi and prev_ref >= 0 and cur is not None and y[prev_ref] is not None:
                wchg = round((cur - y[prev_ref]) * mult, 2)
            y4 = y[ref-20] if ref>=20 else None
            if cur is not None and y4 is not None:
                mom4 = round((cur - y4) * mult, 2)
            if med is not None and cur is not None:
                space = round((med - cur) * mult, 1)
            if n >= MIN_SAMPLE_STATS and cons is not None and mom4 is not None:
                # 顺势程度：共识方向与近4周行情同向的程度
                trend_align = round(cons * (-mom4) / t["sc"], 3)

        # 六层框架统计 + 高频短语
        layer_stats: dict[str, dict[str, Any]] = {
            lay: {"bull": 0, "bear": 0, "neu": 0, "net": 0, "total": 0, "phrases": Counter()}
            for lay in LAYERS
        }
        all_phrases: Counter[str] = Counter()
        for v in vs:
            for dr in v["drivers"]:
                ls = layer_stats[dr["layer"]]
                ls["total"] += 1
                if dr["impact"] == "利多":
                    ls["bull"] += 1
                    ls["net"] += 1
                elif dr["impact"] == "利空":
                    ls["bear"] += 1
                    ls["net"] -= 1
                else:
                    ls["neu"] += 1
                ls["phrases"][dr["phrase"]] += 1
                all_phrases[dr["phrase"]] += 1

        res = {
            "tenor": tid,
            "short": t["short"],
            "type": t["type"],
            "n": n,
            "n_tgt": n_tgt,
            "cons": round(cons, 3) if cons is not None else None,
            "disp": round(disp, 3) if disp is not None else None,
            "bull": bull,
            "neu": neu,
            "bear": bear,
            "cur": cur,
            "wchg": wchg,
            "mom4": mom4,
            "med": round(med, 4) if med is not None else None,
            "space": space,
            "trend_align": trend_align,
            "up": sum(1 for v in vs if v["delta"] == "更偏多"),
            "down": sum(1 for v in vs if v["delta"] == "更偏空"),
            "act": _mode([v["action"] for v in vs]),
            "top_phrases": [p for p, _ in all_phrases.most_common(2)],
            "layers": {
                lay: {
                    "net": ls["net"],
                    "total": ls["total"],
                    "bull": ls["bull"],
                    "bear": ls["bear"],
                    "phrases": [p for p, _ in ls["phrases"].most_common(4)],
                }
                for lay, ls in layer_stats.items()
            },
            "vs": vs,
        }
        mc[(wi, tid)] = res
        return res

    def delta_cons(wi: int, tid: str) -> float | None:
        if wi <= 0:
            return None
        a, b = metrics(wi, tid)["cons"], metrics(wi - 1, tid)["cons"]
        return round(a - b, 3) if (a is not None and b is not None) else None

    # 5) 按周构建周度驾驶舱数据
    dash_by_week: list[dict[str, Any]] = []
    for wi, wk in enumerate(weeks):
        w_views = by_week[wi]
        ref = wk["ref_idx"]
        ref4 = max(0, ref - 20)

        # 六层框架本周净变化（相对前3周均值）
        layer_shift = []
        prev_weeks = list(range(max(0, wi - 3), wi))
        for lay in LAYERS:
            now_net = sum(metrics(wi, tid)["layers"][lay]["net"] for tid in TENOR_IDS)
            now_tot = sum(metrics(wi, tid)["layers"][lay]["total"] for tid in TENOR_IDS)
            now_ratio = (now_net / max(1, len(w_views))) * 100.0
            if prev_weeks:
                p_ratios = []
                for pw in prev_weeks:
                    p_net = sum(metrics(pw, tid)["layers"][lay]["net"] for tid in TENOR_IDS)
                    p_ratios.append((p_net / max(1, len(by_week[pw]))) * 100.0)
                prev_ratio = sum(p_ratios) / len(p_ratios)
            else:
                prev_ratio = 0.0
            ph_cnt: Counter[str] = Counter()
            for v in w_views:
                for dr in v["drivers"]:
                    if dr["layer"] == lay:
                        ph_cnt[dr["phrase"]] += 1
            layer_shift.append({
                "layer": lay,
                "now_net": now_net,
                "now_total": now_tot,
                "now_score": round(now_ratio, 1),
                "prev_score": round(prev_ratio, 1),
                "delta": round(now_ratio - prev_ratio, 1),
                "phrases": [p for p, _ in ph_cnt.most_common(4)],
            })

        # 曲线 × 卖方目标（仅画真正给出数值目标的观点）
        curve_bands, curve_dots, curve_meds = [], [], []
        for i, t in enumerate(YIELD_TENORS):
            m = metrics(wi, t["id"])
            tgt_vs = [v for v in m["vs"] if v["mid"] is not None]
            if len(tgt_vs) >= 2:
                curve_bands.append({
                    "x": i, "tenor": t["id"],
                    "lo": min(v["target_low"] for v in tgt_vs),
                    "hi": max(v["target_high"] for v in tgt_vs),
                    "cons": m["cons"], "n_tgt": len(tgt_vs), "n": m["n"],
                })
            if m["med"] is not None:
                curve_meds.append({
                    "x": i, "tenor": t["id"], "val": m["med"],
                    "cons": m["cons"], "n_tgt": m["n_tgt"], "n": m["n"],
                })
            for v in tgt_vs:
                bi = broker_idx.get(v["broker"], 5)
                curve_dots.append({
                    "x": i, "val": v["mid"], "offset": round((bi - 5.5) * 2.8, 1),
                    "dir": v["direction"], "vid": v["id"], "broker": v["broker"],
                    "tenor": v["tenor"], "raw_tenor": v["raw_tenor"],
                    "lo": v["target_low"], "hi": v["target_high"],
                    "action": v["action"], "horizon": v["horizon"],
                    "phrases": [dr["phrase"] for dr in v["drivers"]],
                })

        curve = {
            "has_yields": ref>=0,
            "tenors": [t["id"] for t in YIELD_TENORS],
            "current": [yields_by_key[t["ykey"]][ref] for t in YIELD_TENORS] if ref>=0 else [],
            "prev4w": [yields_by_key[t["ykey"]][ref4] for t in YIELD_TENORS] if ref>=20 else [],
            "bands": curve_bands,
            "dots": curve_dots,
            "meds": curve_meds,
        }

        # 矩阵行
        matrix = []
        for t in TENORS:
            m = metrics(wi, t["id"])
            hist = [metrics(i, t["id"])["cons"] for i in range(max(0, wi - 7), wi + 1)]
            matrix.append({
                "tenor": t["id"], "short": t["short"], "type": t["type"],
                "cur": m["cur"], "wchg": m["wchg"], "mom4": m["mom4"],
                "cons": m["cons"], "bull": m["bull"], "neu": m["neu"], "bear": m["bear"],
                "n": m["n"], "n_tgt": m["n_tgt"],
                "delta_cons": delta_cons(wi, t["id"]), "up": m["up"], "down": m["down"],
                "hist": hist, "med": m["med"], "space": m["space"],
                "disp": m["disp"], "trend_align": m["trend_align"],
                "act": m["act"], "top_phrases": m["top_phrases"],
            })

        # 本周转向
        changes = [v for v in w_views if v["delta"] in ("更偏多", "更偏空")]
        changes.sort(key=lambda v: (-abs(v["direction"] - (v["prev_dir"] or 0)), TENOR_IDS.index(v["tenor"])))

        # 分歧最大 Top 2（要求 n >= MIN_SAMPLE_STATS）
        div_ms = sorted(
            [metrics(wi, tid) for tid in TENOR_IDS if metrics(wi, tid)["disp"] is not None],
            key=lambda m: -m["disp"],
        )[:2]
        divergences = []
        for m in div_ms:
            sv = sorted(m["vs"], key=lambda v: (-v["direction"], v["mid"] if v["mid"] is not None else 99))
            hi, lo = sv[0], sv[-1]
            divergences.append({
                "tenor": m["tenor"], "type": m["type"], "disp": m["disp"],
                "bull": m["bull"], "neu": m["neu"], "bear": m["bear"],
                "hi_id": hi["id"], "lo_id": lo["id"],
            })

        # 摘要数据
        big_deltas = [(tid, delta_cons(wi, tid)) for tid in TENOR_IDS if delta_cons(wi, tid) is not None]
        big_move = max(big_deltas, key=lambda x: abs(x[1])) if big_deltas else None
        top_layer = max(layer_shift, key=lambda x: abs(x["delta"])) if layer_shift else None

        dash_by_week.append({
            "week_idx": wi,
            "covered_brokers": len({v["broker"] for v in w_views}),
            "total_views": len(w_views),
            "market_date": dates[ref] if ref>=0 else "未接入行情",
            "summary": {
                "short_cons": metrics(wi, "短端(1-3Y)")["cons"],
                "ten_cons": metrics(wi, "10Y国债")["cons"],
                "long_cons": metrics(wi, "30Y超长债")["cons"],
                "curve_cons": metrics(wi, "曲线形态")["cons"],
                "big_move": (
                    {"tenor": big_move[0], "delta": big_move[1], "up": metrics(wi, big_move[0])["up"], "down": metrics(wi, big_move[0])["down"]}
                    if big_move else None
                ),
                "top_diverge": {"tenor": div_ms[0]["tenor"], "disp": div_ms[0]["disp"]} if div_ms else None,
                "top_layer": top_layer,
            },
            "curve": curve,
            "layer_shift": layer_shift,
            "matrix": matrix,
            "change_ids": [v["id"] for v in changes[:12]],
            "extra_changes": max(0, len(changes) - 12),
            "divergences": divergences,
        })

    # 6) 每个期限的周度演变 & 6层热力图（供期限详情页）
    tenor_series: dict[str, Any] = {}
    for t in TENORS:
        tid = t["id"]
        weekly = []
        heat_cells = []
        max_abs = 1
        for wi, wk in enumerate(weeks):
            m = metrics(wi, tid)
            weekly.append({
                "short": wk["short"], "bull": m["bull"], "neu": m["neu"], "bear": m["bear"],
                "cons": m["cons"], "disp": m["disp"],
            })
            for li, lay in enumerate(LAYERS):
                ls = m["layers"][lay]
                max_abs = max(max_abs, abs(ls["net"]))
                if ls["total"] > 0:
                    heat_cells.append({
                        "x": wi, "y": li, "net": ls["net"], "total": ls["total"],
                        "bull": ls["bull"], "bear": ls["bear"], "phrases": ls["phrases"],
                    })
        tenor_series[tid] = {
            "weekly": weekly,
            "heatmap": {"layers": LAYERS, "cells": heat_cells, "max_abs": max_abs},
        }

    # 7) 券商画像（立场倾向 + 观点切换频率 + 覆盖广度，不做易失真的自动胜率）
    broker_stats = []
    for b in brokers:
        bvs = [v for v in views if v["broker"] == b["name"]]
        yv = [v for v in bvs if v["vote_eligible"] and TENOR_MAP[v["tenor"]]["type"] == "yield"]
        wp = [v for v in bvs if v["vote_eligible"] and v["delta"] != "新覆盖"]
        bias = _mean([v["direction"] for v in yv]) or 0.0
        chg = (sum(1 for v in wp if v["delta"] != "维持") / len(wp) * 100.0) if wp else 0.0
        tgt_rate = (sum(1 for v in bvs if v["mid"] is not None) / len(bvs) * 100.0) if bvs else 0.0
        tags = [
            "长期偏多" if bias > 0.35 else ("偏谨慎" if bias < -0.15 else "立场均衡"),
            "观点切换积极" if chg > 35 else ("观点稳定" if chg < 20 else "切换频率中等"),
            "常给具体点位" if tgt_rate >= 55 else "以定性判断为主",
        ]
        broker_stats.append({
            "name": b["name"], "series": b["series"], "n": len(bvs),
            "bias": round(bias, 3), "chg": round(chg, 1), "tgt_rate": round(tgt_rate, 1),
            "tenors": len({v["tenor"] for v in bvs}), "tags": tags,
        })

    # 精简视图列表供前端按需读取（弹窗、卡片、券商热力图）
    return {
        "schema_version": "2.0-static",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "has_yields": has_yields,
        "min_sample_stats": MIN_SAMPLE_STATS,
        "min_sample_target": MIN_SAMPLE_TARGET,
        "tenors": TENORS,
        "layers": LAYERS,
        "brokers": brokers,
        "broker_stats": broker_stats,
        "weeks": weeks,
        "dates": dates,
        "yields": yields_by_key,
        "dash_by_week": dash_by_week,
        "tenor_series": tenor_series,
        "views": views,
    }


def generate_demo_raw_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成符合新 Schema 的模拟原始数据（含空目标点位、开放短语、5期限桶）。"""
    rng = random.Random(20261124)
    brokers = [
        ("招商证券", "利率策略周报", 0.10, 0.55, 0.70),
        ("国联民生", "债券策略周报", 0.35, 0.60, 0.65),
        ("兴业证券", "固收周度观点", 0.20, 0.50, 0.75),
        ("华创证券", "债市周观点", -0.10, 0.50, 0.55),
        ("广发证券", "利率周报", 0.00, 0.45, 0.50),
        ("中信建投", "固收周报", 0.15, 0.55, 0.60),
        ("华泰证券", "利率债周报", -0.20, 0.60, 0.60),
        ("申万宏源", "债市策略周报", 0.05, 0.50, 0.70),
        ("国信证券", "固收周观点", 0.25, 0.65, 0.45),
        ("东吴证券", "利率周度跟踪", 0.45, 0.70, 0.40),
        ("财通证券", "债市周报", -0.30, 0.55, 0.50),
        ("浙商证券", "利率策略周报", -0.15, 0.50, 0.55),
    ]

    # 日度收益率
    dates = []
    d = date(2026, 7, 1)
    while d <= date(2026, 11, 23):
        s = d.isoformat()
        if d.weekday() < 5 and not ("2026-10-01" <= s <= "2026-10-08"):
            dates.append(s)
        d += timedelta(days=1)

    lv = sv = sh = 0.0
    ym = {"1Y": (1.15, 0.7, -1.0, 1.0), "5Y": (1.45, 0.95, -0.3, 0.3), "10Y": (1.65, 1.0, 0.2, 0.0), "30Y": (1.95, 1.1, 0.8, 0.0)}
    raw_y = {k: [] for k in ym}
    for s in dates:
        m = int(s[5:7])
        lv += {7: -0.1, 8: 0.2, 9: 0.3, 10: -0.45, 11: -0.25}[m] + 1.1 * rng.gauss(0, 1)
        sv += 0.5 * rng.gauss(0, 1) - 0.02 * sv
        if "2026-10-30" <= s <= "2026-11-10":
            sh -= 1.5
        for k, (_, cL, cS, cSh) in ym.items():
            raw_y[k].append(lv * cL + sv * cS + sh * cSh + 0.35 * rng.gauss(0, 1))

    y_norm = {k: [round((v + ym[k][0] * 100 - raw_y[k][-1]) / 100.0, 4) for v in raw_y[k]] for k in ym}
    yield_rows = []
    for i, s in enumerate(dates):
        yield_rows.append({
            "date": s,
            "1Y": y_norm["1Y"][i],
            "5Y": y_norm["5Y"][i],
            "10Y": y_norm["10Y"][i],
            "30Y": y_norm["30Y"][i],
            "30-10": round((y_norm["30Y"][i] - y_norm["10Y"][i]) * 100.0, 2),
        })

    # 模拟开放短语池（按六层分类）
    phrase_pool = {
        "基本面": [("社融与信贷偏弱", "利多"), ("地产销售拖累", "利多"), ("通胀维持低位", "利多"), ("出口韧性超预期", "利空")],
        "政策面": [("央行隔夜换锚", "利多"), ("降息预期升温", "利多"), ("财政发力加码", "利空"), ("稳增长政策落地", "利空")],
        "资金面": [("资金中枢下移", "利多"), ("存单利率下行", "利多"), ("跨月资金扰动", "利空"), ("央行呵护流动性", "利多")],
        "机构行为": [("银行配置盘承接", "利多"), ("保险增配超长债", "利多"), ("债基赎回负反馈", "利空"), ("交易盘持仓拥挤", "利空")],
        "供给面": [("年内供给压力缓和", "利多"), ("特别国债集中发行", "利空"), ("地方债供给放量", "利空")],
        "估值情绪": [("骑乘性价比凸显", "利多"), ("股债跷跷板压制", "利空"), ("利差保护偏薄", "利空"), ("曲线偏平防回调", "利空")],
    }
    raw_tenors = {
        "短端(1-3Y)": ["1年国债", "1-3年利率债", "短端利率债(期限未细分)"],
        "中端(5-7Y)": ["5年国债", "5-7年利率骑乘", "中端利率债"],
        "10Y国债": ["10年国债", "10年国债活跃券"],
        "30Y超长债": ["30年国债", "30年超长特别国债"],
        "曲线形态": ["30Y-10Y利差", "10Y-1Y期限利差"],
    }
    cover_prob = {"短端(1-3Y)": 0.65, "中端(5-7Y)": 0.55, "10Y国债": 0.95, "30Y超长债": 0.85, "曲线形态": 0.55}

    views = []
    last_dir: dict[tuple[str, str], int] = {}
    wd = date(2026, 7, 27)
    while wd <= date(2026, 11, 23):
        mon = wd.isoformat()
        ref = max((i for i, s in enumerate(dates) if s < mon), default=0)
        recent = wd >= date(2026, 11, 2)
        for name, series, bias, persist, tgt_prob in brokers:
            if rng.random() > 0.88:
                continue
            rd = (wd - timedelta(days=1)).isoformat() if rng.random() < 0.65 else mon
            for t in TENORS:
                if rng.random() > cover_prob[t["id"]]:
                    continue
                score = bias + 0.75 * rng.gauss(0, 1)
                if recent and t["id"] == "短端(1-3Y)":
                    score += 0.9
                if recent and t["id"] == "30Y超长债":
                    score += 0.9 * rng.gauss(0, 1)
                if recent and t["id"] == "曲线形态":
                    score += 0.5
                direction = max(-2, min(2, round(score)))
                prev = last_dir.get((name, t["id"]))
                if prev is not None and rng.random() < persist and abs(score - prev) < 1.5:
                    direction = prev
                last_dir[(name, t["id"])] = direction

                cur = yield_rows[ref][t["ykey"]]
                has_num = rng.random() < tgt_prob
                if has_num:
                    if t["type"] == "spread":
                        mid = cur + (-direction * rng.uniform(2, 5))
                        lo, hi = round(mid - 3, 0), round(mid + 3, 0)
                        t_type, t_note = "明确区间", f"{lo:.0f}-{hi:.0f}bp"
                    else:
                        mid = cur + (-direction * rng.uniform(0.03, 0.06))
                        lo, hi = round(mid - 0.04, 2), round(mid + 0.04, 2)
                        t_type, t_note = "明确区间", f"{lo:.2f}%-{hi:.2f}%"
                else:
                    lo, hi = None, None
                    if rng.random() < 0.35 and direction != 0:
                        t_type, t_note = "相对变动bp", f"预计仍有{rng.randint(3,8)}bp{'下行' if direction>0 else '回调'}空间"
                    else:
                        t_type, t_note = "无点位", "未给具体点位"

                # 选 2-3 个驱动（6层 + 开放短语）
                want = "利多" if direction > 0 else ("利空" if direction < 0 else None)
                chosen_layers = rng.sample(LAYERS, k=rng.choice([2, 3]))
                drivers = []
                for lay in chosen_layers:
                    cands = [x for x in phrase_pool[lay] if want is None or x[1] == want] or phrase_pool[lay]
                    ph, imp = rng.choice(cands)
                    drivers.append({"layer": lay, "impact": imp, "phrase": ph})

                if t["type"] == "spread":
                    action = "做平曲线" if direction > 0 else ("做陡曲线" if direction < 0 else "曲线中性")
                else:
                    action = {2: "加仓/拉久期", 1: "逢调加仓" if t["id"] != "中端(5-7Y)" else "骑乘策略", 0: rng.choice(["区间波段", "持有观望"]), -1: "止盈/降久期", -2: "减仓防守"}[direction]

                rt = rng.choice(raw_tenors[t["id"]])
                hz = rng.choice(["短期(1-2周)", "1个月", "季度/中长期", "条件触发"])
                risk = rng.choice(["跨月资金收敛", "股债跷跷板强化", "供给节奏超预期", "止盈盘集中兑现"])
                views.append({
                    "broker": name, "series": series, "report_date": rd, "week_mon": mon,
                    "pdf_url": "",
                    "tenor": t["id"], "raw_tenor": rt, "direction": direction, "action": action,
                    "target_type": t_type, "target_low": lo, "target_high": hi, "target_note": t_note,
                    "horizon": hz, "drivers": drivers, "risk": risk,
                    "logic": f"{'、'.join(x['phrase'] for x in drivers)}主导下，{rt}策略建议{action}（{t_note}）。",
                    "cond": f"若{risk}超预期则修正判断。",
                    "quote": f"“当前{drivers[0]['phrase']}仍是核心主线，对{rt}建议{action}，{t_note}，同时警惕{risk}。”",
                    "page": rng.randint(2, 8),
                })
        wd += timedelta(days=7)

    return views, yield_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static snapshot.json for Bond Dashboard")
    parser.add_argument("--seed-demo", action="store_true", help="Generate demo data/views.json & data/yields.json first")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    views_path = DATA_DIR / "views.json"
    yields_path = DATA_DIR / "yields.json"

    if args.seed_demo:
        demo_views, demo_yields = generate_demo_raw_data()
        views_path.write_text(json.dumps(demo_views, ensure_ascii=False, indent=2), encoding="utf-8")
        yields_path.write_text(json.dumps(demo_yields, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote demo raw data: {len(demo_views)} views, {len(demo_yields)} yield days")

    raw_views = json.loads(views_path.read_text(encoding="utf-8")) if views_path.exists() else []
    raw_yields = json.loads(yields_path.read_text(encoding="utf-8")) if yields_path.exists() else []
    snapshot = build_snapshot(raw_views, raw_yields)
    OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Built {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB, {len(snapshot['views'])} views, {len(snapshot['weeks'])} weeks)")


if __name__ == "__main__":
    main()
