"""
Pharma Market Analytics - automated pipeline
=============================================
One command takes the raw CSVs all the way to an interactive dashboard:

    python pipeline/run_pipeline.py            # rule-based insight agent
    python pipeline/run_pipeline.py --llm      # optional: LLM writes the executive summary

Steps
  1. Validate      - schema, nulls, negatives, duplicates, referential integrity
  2. Load          - CSVs into a SQLite database (outputs/pharma.db)
  3. Query         - runs the KPI SQL in sql/pharma_kpi_queries.sql
  4. Compute       - market share, growth, Evolution Index, penetration (2025 vs 2024)
  5. Insight agent - turns the KPIs into plain-English findings and recommendations
  6. Build         - writes outputs/dashboard.html (interactive, opens in any browser)

Data note: the dataset is SYNTHETIC SAMPLE DATA with fictional brands and companies.
"""
import argparse, json, os, sqlite3, sys, urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA, SQL, OUT = ROOT / "data", ROOT / "sql", ROOT / "outputs"
TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"
CUR, PREV = "2025", "2024"

SCHEMA = {
    "fact_sales": ["month", "brand_id", "region", "ta_region_key", "units", "value_inr", "prescribing_doctors"],
    "dim_brand": ["brand_id", "brand", "company", "molecule", "therapy_area"],
    "dim_doctor_universe": ["ta_region_key", "therapy_area", "region", "target_doctors"],
}


def log(step, msg):
    print(f"[{step}] {msg}")


def find_file(expected: Path) -> Path:
    """Return the file at its expected path, or search the project for it by name.
    Helps when a zip is extracted with an extra or missing folder level."""
    if expected.exists():
        return expected
    for base in (ROOT, ROOT.parent):
        hits = list(base.rglob(expected.name))
        if hits:
            return hits[0]
    sys.exit(f"Could not find {expected.name}. Expected it at:\n  {expected}\n"
             f"Check that the '{expected.parent.name}' folder is inside {ROOT}")


# 1. VALIDATE -----------------------------------------------------------------
def validate(tables):
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    for t, cols in SCHEMA.items():
        missing = set(cols) - set(tables[t].columns)
        add(f"{t}: required columns", not missing, "all present" if not missing else f"missing {sorted(missing)}")
        nulls = int(tables[t][cols].isna().sum().sum()) if not missing else -1
        add(f"{t}: no null values", nulls == 0, f"{nulls} nulls")

    f = tables["fact_sales"]
    neg = int((f[["units", "value_inr", "prescribing_doctors"]] < 0).sum().sum())
    add("fact_sales: no negative values", neg == 0, f"{neg} negative cells")
    dup = int(f.duplicated(["month", "brand_id", "region"]).sum())
    add("fact_sales: one row per month-brand-region", dup == 0, f"{dup} duplicates")
    orphan_b = int((~f.brand_id.isin(tables["dim_brand"].brand_id)).sum())
    add("fact_sales -> dim_brand keys match", orphan_b == 0, f"{orphan_b} orphan rows")
    orphan_u = int((~f.ta_region_key.isin(tables["dim_doctor_universe"].ta_region_key)).sum())
    add("fact_sales -> doctor universe keys match", orphan_u == 0, f"{orphan_u} orphan rows")
    months = pd.to_datetime(f.month)
    exp = pd.date_range(months.min(), months.max(), freq="MS")
    add("continuous monthly coverage", months.nunique() == len(exp), f"{months.min():%b %Y} to {months.max():%b %Y}, {months.nunique()} months")
    m = f.merge(tables["dim_doctor_universe"], on="ta_region_key")
    over = int((m.prescribing_doctors > m.target_doctors).sum())
    add("prescribers never exceed target universe", over == 0, f"{over} rows over universe")
    return checks


# 2-3. LOAD + QUERY -------------------------------------------------------------
def load_and_query(tables):
    OUT.mkdir(exist_ok=True)
    db = OUT / "pharma.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    for name, df in tables.items():
        df.to_sql(name, con, index=False)
    statements = [s.strip() for s in find_file(SQL / "pharma_kpi_queries.sql").read_text(encoding="utf-8").split(";") if "SELECT" in s.upper()]
    results = [pd.read_sql(s, con) for s in statements]
    return con, results


# 4. COMPUTE ------------------------------------------------------------------
def compute(tables):
    f = tables["fact_sales"].merge(tables["dim_brand"], on="brand_id")
    f["year"] = f.month.str[:4]
    uni = tables["dim_doctor_universe"]
    out = {}
    for ta, g in f.groupby("therapy_area"):
        brands = tables["dim_brand"][tables["dim_brand"].therapy_area == ta].sort_values("brand_id").brand.tolist()
        mk = g.groupby("year").value_inr.sum()
        by = g.groupby(["brand", "year"]).value_inr.sum().unstack()
        rows = []
        for b in brands:
            bg = by.loc[b, CUR] / by.loc[b, PREV] - 1
            mg = mk[CUR] / mk[PREV] - 1
            pres = g[(g.brand == b) & (g.year == CUR)].groupby("month").prescribing_doctors.sum().mean()
            tgt = uni[uni.therapy_area == ta].target_doctors.sum()
            rows.append(dict(brand=b, company=g[g.brand == b].company.iloc[0], molecule=g[g.brand == b].molecule.iloc[0],
                             sales_cr=by.loc[b, CUR] / 1e7, share=by.loc[b, CUR] / mk[CUR],
                             brand_growth=bg, market_growth=mg, ei=(1 + bg) / (1 + mg) * 100, penetration=pres / tgt))
        kpi = pd.DataFrame(rows)
        monthly = g.groupby(["month", "brand"]).value_inr.sum().unstack()
        share_trend = monthly.div(monthly.sum(axis=1), axis=0)
        reg = (g[g.year == CUR].groupby(["brand", "region"]).prescribing_doctors.mean().reset_index()
               .merge(uni[uni.therapy_area == ta], on="region"))
        reg["pen"] = reg.prescribing_doctors / reg.target_doctors
        regions = ["North", "South", "East", "West"]
        heat = reg.pivot(index="brand", columns="region", values="pen").reindex(index=brands, columns=regions)
        mkt_month = g.groupby("month").value_inr.sum() / 1e7
        out[ta] = dict(
            brands=brands, kpi=kpi, market_cr=mk[CUR] / 1e7, market_prev_cr=mk[PREV] / 1e7,
            market_growth=mk[CUR] / mk[PREV] - 1, months=list(share_trend.index),
            share_trend={b: [round(v * 100, 2) for v in share_trend[b]] for b in brands},
            market_month=[round(v, 2) for v in mkt_month], regions=regions,
            heat=[[round(v * 100, 1) for v in heat.loc[b]] for b in brands],
        )
    return out


# 5. INSIGHT AGENT ------------------------------------------------------------
def rule_insights(ta, d):
    k = d["kpi"].set_index("brand")
    pct = lambda x: f"{x * 100:.1f}%"
    ins = []
    leader = k.share.idxmax()
    ins.append(f"The {ta.lower()} market is worth ₹{d['market_cr']:.1f} Cr in {CUR}, growing {pct(d['market_growth'])} vs {PREV}. "
               f"{leader} leads with {pct(k.share[leader])} share.")
    gainer = k.ei.idxmax()
    ins.append(f"{gainer} is the fastest share-gainer (Evolution Index {k.ei[gainer]:.0f}): it grew {pct(k.brand_growth[gainer])} "
               f"against market growth of {pct(d['market_growth'])}.")
    hidden = k[(k.brand_growth > 0) & (k.ei < 100)]
    for b in hidden.index:
        ins.append(f"{b} looks healthy on sales (+{pct(k.brand_growth[b])}) but its EI is {k.ei[b]:.1f}, so it is quietly losing share. "
                   f"Recommend reviewing positioning against faster-growing brands.")
    losers = k[(k.brand_growth < 0)]
    for b in losers.index:
        ins.append(f"{b} is declining in absolute sales ({pct(k.brand_growth[b])}, EI {k.ei[b]:.0f}).")
    gap = (k.penetration.rank(ascending=False) - k.share.rank(ascending=False))
    b = gap.idxmin()
    if gap[b] <= -2:
        ins.append(f"{b} has one of the highest prescriber penetrations ({pct(k.penetration[b])}) but only {pct(k.share[b])} share: "
                   f"many doctors try it but prescribe low volumes. Opportunity: deepen usage among existing prescribers.")
    heat = pd.DataFrame(d["heat"], index=d["brands"], columns=d["regions"])
    row = heat.loc[gainer]
    ins.append(f"For {gainer}, penetration is highest in {row.idxmax()} ({row.max():.1f}%) and lowest in {row.idxmin()} ({row.min():.1f}%), "
               f"the clearest region to target next.")
    return ins


def llm_summary(payload):
    """Optional: send KPIs to any OpenAI-compatible endpoint for an executive summary.
    Needs LLM_BASE_URL, LLM_API_KEY and LLM_MODEL environment variables. Falls back silently."""
    base, key, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not (base and key and model):
        log("agent", "LLM settings not found; using rule-based insights only")
        return None
    prompt = ("You are a pharma commercial analytics consultant. Using ONLY these KPIs, write a 4-sentence executive "
              "summary for a brand manager, citing exact numbers. Do not invent data.\n" + json.dumps(payload))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"].strip()
    except Exception as e:  # never let the optional step break the pipeline
        log("agent", f"LLM call failed ({e}); using rule-based insights only")
        return None


# 6. BUILD --------------------------------------------------------------------
def build(results, checks, insights, summaries):
    payload = {"generated": datetime.now().strftime("%d %b %Y, %H:%M"), "cur": CUR, "prev": PREV, "ta": {}, "checks": checks}
    for ta, d in results.items():
        k = d["kpi"]
        payload["ta"][ta] = dict(
            brands=d["brands"], market_cr=round(d["market_cr"], 2), market_prev_cr=round(d["market_prev_cr"], 2),
            market_growth=round(d["market_growth"] * 100, 1), months=d["months"], share_trend=d["share_trend"],
            market_month=d["market_month"], regions=d["regions"], heat=d["heat"],
            kpi=[dict(brand=r.brand, company=r.company, molecule=r.molecule, sales_cr=round(r.sales_cr, 2),
                      share=round(r.share * 100, 1), brand_growth=round(r.brand_growth * 100, 1),
                      market_growth=round(r.market_growth * 100, 1), ei=round(r.ei, 1), penetration=round(r.penetration * 100, 1))
                 for r in k.itertuples()],
            insights=insights[ta], summary=summaries.get(ta),
        )
    html = find_file(TEMPLATE).read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(payload))
    (OUT / "dashboard.html").write_text(html, encoding="utf-8")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", action="store_true", help="also ask an LLM to write an executive summary")
    args = ap.parse_args()

    tables = {t: pd.read_csv(find_file(DATA / f"{t}.csv")) for t in SCHEMA}
    log("1/6 validate", f"loaded {', '.join(f'{t} ({len(df)} rows)' for t, df in tables.items())}")
    checks = validate(tables)
    for c in checks:
        log("1/6 validate", f"{c['status']}  {c['check']}  ({c['detail']})")
    if any(c["status"] == "FAIL" for c in checks):
        sys.exit("Data validation failed - fix the data before building the dashboard.")

    con, sql_results = load_and_query(tables)
    log("2/6 load", "SQLite database written to outputs/pharma.db")
    log("3/6 query", f"ran {len(sql_results)} KPI queries from sql/pharma_kpi_queries.sql")

    results = compute(tables)
    kpi_all = pd.concat([d["kpi"].assign(therapy_area=ta) for ta, d in results.items()])
    kpi_all.round(4).to_csv(OUT / "kpi_summary.csv", index=False)
    # cross-check: Python KPIs must match the SQL Evolution Index query
    sql_ei = sql_results[2].set_index("brand").evolution_index
    diff = (kpi_all.set_index("brand").ei.round(1) - sql_ei).abs().max()
    log("4/6 compute", f"KPIs computed; max difference vs SQL Evolution Index = {diff:.2f}")

    insights = {ta: rule_insights(ta, d) for ta, d in results.items()}
    summaries = {}
    if args.llm:
        for ta, d in results.items():
            summaries[ta] = llm_summary({"therapy_area": ta, "market_cr": round(d["market_cr"], 2),
                                         "market_growth_pct": round(d["market_growth"] * 100, 1),
                                         "brands": d["kpi"].round(3).to_dict("records")})
    with open(OUT / "insights.md", "w", encoding="utf-8") as fh:
        fh.write("# Key insights (sample data)\n\n")
        for ta, lines in insights.items():
            fh.write(f"## {ta}\n\n")
            if summaries.get(ta):
                fh.write(f"**Executive summary (LLM):** {summaries[ta]}\n\n")
            fh.writelines(f"- {l}\n" for l in lines)
            fh.write("\n")
    log("5/6 insights", f"wrote {sum(len(v) for v in insights.values())} findings to outputs/insights.md")

    with open(OUT / "data_quality_report.md", "w", encoding="utf-8") as fh:
        fh.write("# Data quality report\n\n| Check | Status | Detail |\n|---|---|---|\n")
        fh.writelines(f"| {c['check']} | {c['status']} | {c['detail']} |\n" for c in checks)

    build(results, checks, insights, summaries)
    log("6/6 build", "dashboard written to outputs/dashboard.html - open it in a browser")
    con.close()


if __name__ == "__main__":
    main()
