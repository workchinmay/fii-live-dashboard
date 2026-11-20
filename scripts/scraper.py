import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import pandas as pd
import requests

NSDL_URL = "https://www.fpi.nsdl.co.in/Reports/Latest.aspx"
CDSL_URL = "https://www.cdslindia.com/publications/FIIDailyData.aspx"
OUTPUT_PATH = Path("data/fii_history.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}

def clean_num(val):
    if pd.isna(val):
        return 0.0
    s = re.sub(r"[^\d.\-]", "", str(val))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0

def fetch_depository(url: str, source: str):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    if len(tables) < 2:
        raise RuntimeError(f"{source}: Expected 2 tables, got {len(tables)}")

    # Table 0: Cash / Debt / Hybrid
    t0 = tables[0].copy()
    t0.columns = [f"c{i}" for i in range(t0.shape[1])]
    dt = pd.to_datetime(str(t0.iloc[0, 0]).strip(), dayfirst=True)
    iso_date = dt.strftime("%Y-%m-%d")
    display_date = dt.strftime("%d %b %Y")
    t0["c1"] = t0["c1"].ffill()

    cash = {}
    usd_inr = 0.0
    for _, row in t0.iterrows():
        asset = str(row["c1"]).strip()
        route = str(row["c2"]).strip()
        if any(w in route for w in ["Sub-total", "Total"]) or "Reporting" in asset:
            continue
        if usd_inr == 0.0 and clean_num(row.get("c7", 0)) > 50:
            usd_inr = clean_num(row["c7"])
        tag = "Secondary" if "Stock" in route else "Primary"
        cash[f"{asset}_{tag}"] = {
            "buy_cr": clean_num(row["c3"]),
            "sell_cr": clean_num(row["c4"]),
            "net_cr": clean_num(row["c5"]),
            "net_usd_mn": clean_num(row["c6"]),
        }

    # Table 1: FPI Derivatives
    t1 = tables[1].copy()
    t1.columns = [f"c{i}" for i in range(t1.shape[1])]
    derivs = {}
    for _, row in t1.iterrows():
        prod = str(row["c1"]).strip().replace("_", " ").title()
        if any(k in prod for k in ["Index Futures", "Index Options", "Stock Futures", "Stock Options"]):
            buy_cr = clean_num(row["c3"])
            sell_cr = clean_num(row["c5"])
            derivs[prod] = {
                "buy_cr": buy_cr,
                "sell_cr": sell_cr,
                "net_cr": round(buy_cr - sell_cr, 2),
                "oi_contracts": int(clean_num(row["c6"])),
                "oi_cr": clean_num(row["c7"]),
            }

    eq_sec = cash.get("Equity_Secondary", {}).get("net_cr", 0.0)
    eq_pri = cash.get("Equity_Primary", {}).get("net_cr", 0.0)
    debt_net = sum(v["net_cr"] for k, v in cash.items() if k.startswith("Debt"))

    return iso_date, {
        "date": iso_date,
        "display_date": display_date,
        "source": source,
        "usd_inr": usd_inr,
        "summary": {
            "equity_secondary_cr": round(eq_sec, 2),
            "equity_primary_cr": round(eq_pri, 2),
            "equity_total_cr": round(eq_sec + eq_pri, 2),
            "debt_total_cr": round(debt_net, 2),
        },
        "derivatives": derivs,
    }

def main():
    try:
        iso_date, rec = fetch_depository(NSDL_URL, "NSDL")
    except Exception as e:
        print(f"NSDL failed ({e}), switching to CDSL...")
        iso_date, rec = fetch_depository(CDSL_URL, "CDSL")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = json.loads(OUTPUT_PATH.read_text()) if OUTPUT_PATH.exists() else {"updated_utc": "", "records": {}}
    store["records"][iso_date] = rec
    store["records"] = dict(sorted(store["records"].items()))
    store["updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUTPUT_PATH.write_text(json.dumps(store, indent=2))
    print(f"Saved {iso_date} from {rec['source']}")

if __name__ == "__main__":
    main()
