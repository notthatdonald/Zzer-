#!/usr/bin/env python3
"""
Build the handbag dataset, price history and reports from scraper output.

Inputs
  data/snapshots/<date>/handbags.jsonl   (scraper/scrape.js)
  data/snapshots/<date>/facets.json
  data/details/details.jsonl             (scraper/details.js, optional)

Outputs
  data/zzer.sqlite                       listings, price_history, facets tables (all runs)
  reports/<date>/handbags.csv            one row per handbag, all fields
  reports/<date>/brand_summary.csv       SKU counts + price/discount stats per brand
  reports/<date>/zzer_handbags_<date>.xlsx  Excel workbook with all tables
  reports/<date>/summary.json            numbers used by the dashboard

Usage:  python3 analysis/analyze.py [--date YYYY-MM-DD]
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAP = DATA / "snapshots"
IMG = "https://img.goshare2.com/"

CONDITION_EN = {
    "全新": "New", "未使用": "Unused", "99新": "9.9", "9.9新": "9.9", "98新": "9.8", "9.8新": "9.8",
    "95新": "9.5", "9.5新": "9.5", "93新": "9.3", "9.3新": "9.3", "9新": "9.0", "9.0新": "9.0",
    "85新": "8.5", "8.5新": "8.5", "8新": "8.0", "8.0新": "8.0", "7新": "7.0", "7.0新": "7.0",
}
SUBCAT_ZH = {"单肩包": "Shoulder Bags", "手提包": "Tote Bags", "斜挎包": "Crossbody Bags", "腰包": "Belt Bags",
             "双肩包": "Backpacks", "钱包": "Wallets", "手拿包": "Clutches", "卡包": "Cardholders"}


def latest_date():
    return sorted(d.name for d in SNAP.iterdir() if d.is_dir() and d.name[:2] == "20")[-1]


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def build_listings(date):
    raw = pd.DataFrame(load_jsonl(SNAP / date / "handbags.jsonl"))
    df = pd.DataFrame({
        "id": raw["id"].astype(str),
        "sku": raw["sku"].astype(str),
        "brand": raw["brandName"],
        "name_zh": raw["name"],
        "subcategory": raw.get("subcategory"),
        "condition_zh": raw["degreeName"],
        "size": raw.get("sizeNameEn").replace("", None).fillna(raw.get("sizeName")),
        "gender": raw.get("gender"),
        "color": raw.get("color"),
        "location": raw.get("location"),
        "model": raw["collection"] if "collection" in raw else None,
        # Prices are CNY. price = listed price; marketPrice = original retail;
        # originalPrice = listed price before platform coupon; couponAmount = coupon.
        "price_cny": pd.to_numeric(raw["originalPrice"], errors="coerce"),
        "price_after_coupon_cny": pd.to_numeric(raw["price"], errors="coerce"),
        "coupon_cny": pd.to_numeric(raw.get("couponAmount"), errors="coerce"),
        "retail_cny": pd.to_numeric(raw["marketPrice"], errors="coerce"),
        "site_discount_zhe": pd.to_numeric(raw["discount"], errors="coerce") / 10,
        "status": raw["status"],
        "stock": raw["stock"],
        "listed_at": pd.to_datetime(raw["firstPutonTime"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None),
        "warehouse_shelf": raw.get("storeProductPositionTextEn"),
        "authenticated": raw.get("identifyType").eq(1),
        "has_video": raw.get("videoUrl").fillna("").ne(""),
        "image_url": IMG + raw["ico"].fillna(""),
        "url": "https://mix.goshare2.com/wv/buy/product/detail/?id=" + raw["id"].astype(str),
        "scraped_at": raw["scrapedAt"],
    })
    df["condition"] = df["condition_zh"].map(CONDITION_EN).fillna(df["condition_zh"])
    df.loc[df["retail_cny"] <= 0, "retail_cny"] = pd.NA
    df["discount_pct"] = (1 - df["price_cny"] / df["retail_cny"]) * 100
    df["discount_after_coupon_pct"] = (1 - df["price_after_coupon_cny"] / df["retail_cny"]) * 100
    df["savings_cny"] = df["retail_cny"] - df["price_cny"]
    snap_day = pd.Timestamp(raw["scrapedAt"].iloc[0][:10])
    df["days_listed"] = (snap_day - df["listed_at"].dt.normalize()).dt.days
    return df


def merge_details(df):
    path = DATA / "details" / "details.jsonl"
    if not path.exists():
        return df
    det = {}
    for r in load_jsonl(path):
        det[str(r["id"])] = r  # last fetch wins
    if not det:
        return df
    rows = []
    for i in df["id"]:
        r = det.get(i)
        if not r or r.get("unavailable"):
            rows.append({"id": i})
            continue
        a = r.get("attrs", {})
        rows.append({
            "id": i,
            "collection_zh": a.get("Collection"),
            "material_zh": a.get("Material"),
            "weight_g": pd.to_numeric(str(a.get("Weight", "")).rstrip("g"), errors="coerce"),
            "length_cm": pd.to_numeric(a.get("m_包身长度"), errors="coerce"),
            "height_cm": pd.to_numeric(a.get("m_包身高度"), errors="coerce"),
            "depth_cm": pd.to_numeric(a.get("m_包身厚度"), errors="coerce"),
            "strap_cm": pd.to_numeric(a.get("m_肩带长度"), errors="coerce"),
            "serial_number": a.get("Serial Number"),
            "accessories": ", ".join(r.get("accessories") or []),
            "n_flaws": len(r.get("flaws") or []),
            "flaws_zh": "; ".join(r.get("flaws") or []),
            "maintenance": "; ".join(r.get("maintenance") or []),
            "condition_note": r.get("degreeExtEn"),
            "bargain_floor_cny": r.get("bargainBasePrice") or None,
            "accepts_offers": str(r.get("openBargain")) == "1",
            "historic_max_price_cny": r.get("historyMaxPrice") or None,
            "seller_grade_zh": r.get("sellerGrade"),
            "seller_listings": r.get("sellerOnSale"),
            "seller_id": r.get("sellerId"),
            "brand_id": r.get("brandId"),
            "inspected_on": r.get("inspectedOn"),
            "image_count": r.get("imageCount"),
            "details_fetched_at": r.get("fetchedAt"),
        })
    df = df.merge(pd.DataFrame(rows), on="id", how="left")
    if "collection_zh" in df:
        df["model"] = df["model"].fillna(df["collection_zh"]) if "model" in df else df["collection_zh"]
    return df


def update_db(df, facets, date):
    con = sqlite3.connect(DATA / "zzer.sqlite")
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS price_history (
        snapshot_date TEXT, id TEXT, price_cny REAL, price_after_coupon_cny REAL, retail_cny REAL,
        discount_pct REAL, status INTEGER, PRIMARY KEY (snapshot_date, id));
    CREATE TABLE IF NOT EXISTS facets (snapshot_date TEXT, facet TEXT, value_id TEXT, value TEXT, count INTEGER);
    """)
    cur.execute("DELETE FROM price_history WHERE snapshot_date=?", (date,))
    cur.execute("DELETE FROM facets WHERE snapshot_date=?", (date,))
    hist = df[["id", "price_cny", "price_after_coupon_cny", "retail_cny", "discount_pct", "status"]].copy()
    hist.insert(0, "snapshot_date", date)
    hist.to_sql("price_history", con, if_exists="append", index=False)

    # listings: latest known attributes for every handbag ever seen
    cols = [c for c in df.columns if df[c].dtype != "object" or c not in ("subcategories",)]
    cur_list = df[cols].copy()
    cur_list["last_seen"] = date
    try:
        old = pd.read_sql("SELECT * FROM listings", con)
        first_seen = dict(zip(old["id"], old["first_seen"]))
        old = old[~old["id"].isin(cur_list["id"])]
    except Exception:
        old, first_seen = pd.DataFrame(), {}
    cur_list["first_seen"] = cur_list["id"].map(first_seen).fillna(date)
    all_list = pd.concat([old, cur_list.astype({c: str for c in cur_list.columns if str(cur_list[c].dtype).startswith("datetime")})], ignore_index=True)
    all_list.to_sql("listings", con, if_exists="replace", index=False)

    rows = []
    for tab in (facets.get("tabs") or []):
        groups = tab.get("allTabs") or [tab]
        for g in groups:
            for v in g.get("tabs") or []:
                rows.append((date, tab.get("name"), str(v.get("id")), v.get("name"), v.get("count")))
    cur.executemany("INSERT INTO facets VALUES (?,?,?,?,?)", rows)
    con.commit()
    return con


def price_changes(con, date):
    """Compare this snapshot with the previous one: price drops, new, sold/removed."""
    dates = [r[0] for r in con.execute("SELECT DISTINCT snapshot_date FROM price_history ORDER BY 1")]
    if date not in dates or dates.index(date) == 0:
        return None
    prev = dates[dates.index(date) - 1]
    q = """
    SELECT c.id, p.price_cny AS prev_price, c.price_cny AS price, c.retail_cny,
           c.price_cny - p.price_cny AS change_cny,
           (c.price_cny - p.price_cny) * 100.0 / p.price_cny AS change_pct
    FROM price_history c JOIN price_history p ON p.id = c.id AND p.snapshot_date = ?
    WHERE c.snapshot_date = ? AND c.price_cny != p.price_cny"""
    changed = pd.read_sql(q, con, params=(prev, date))
    new = pd.read_sql("SELECT id FROM price_history WHERE snapshot_date=? AND id NOT IN (SELECT id FROM price_history WHERE snapshot_date=?)", con, params=(date, prev))
    gone = pd.read_sql("SELECT id, price_cny, retail_cny FROM price_history WHERE snapshot_date=? AND id NOT IN (SELECT id FROM price_history WHERE snapshot_date=?)", con, params=(prev, date))
    return {"previous": prev, "changed": changed, "new": new, "gone": gone}


def summaries(df):
    g = df.groupby("brand")
    brand = pd.DataFrame({
        "skus": g.size(),
        "share_pct": g.size() / len(df) * 100,
        "median_price_cny": g["price_cny"].median(),
        "mean_price_cny": g["price_cny"].mean(),
        "min_price_cny": g["price_cny"].min(),
        "max_price_cny": g["price_cny"].max(),
        "median_retail_cny": g["retail_cny"].median(),
        "median_discount_pct": g["discount_pct"].median(),
        "mean_discount_pct": g["discount_pct"].mean(),
        "p25_discount_pct": g["discount_pct"].quantile(0.25),
        "p75_discount_pct": g["discount_pct"].quantile(0.75),
        "inventory_value_cny": g["price_cny"].sum(),
        "retail_value_cny": g["retail_cny"].sum(),
        "median_days_listed": g["days_listed"].median(),
        "listed_last_7d": g["days_listed"].apply(lambda s: (s <= 7).sum()),
    }).sort_values("skus", ascending=False)
    brand["rank"] = range(1, len(brand) + 1)

    def pivot(col):
        if col not in df or df[col].isna().all():
            return pd.DataFrame()
        s = df.assign(**{col: df[col].fillna("(unknown)").str.split(r" \| ")}).explode(col)
        return pd.crosstab(s["brand"], s[col]).loc[brand.index]

    cond_order = ["New", "Unused", "9.9", "9.8", "9.5", "9.3", "9.0", "8.5", "8.0", "7.0"]
    cond = df.groupby("condition").agg(skus=("id", "size"), median_price_cny=("price_cny", "median"),
                                       median_discount_pct=("discount_pct", "median"))
    cond = cond.reindex([c for c in cond_order if c in cond.index] + [c for c in cond.index if c not in cond_order])
    sub = df.groupby("subcategory").agg(skus=("id", "size"), median_price_cny=("price_cny", "median"),
                                        median_discount_pct=("discount_pct", "median")).sort_values("skus", ascending=False)
    bands = pd.cut(df["discount_pct"], [-1000, 0, 25, 50, 60, 70, 80, 90, 100],
                   labels=["≤0% (at/above retail)", "0–25%", "25–50%", "50–60%", "60–70%", "70–80%", "80–90%", "90%+"])
    disc_bands = bands.value_counts().sort_index().rename("skus").to_frame()
    brand_cond = pd.crosstab(df["brand"], df["condition"]).loc[brand.index]
    brand_cond = brand_cond[[c for c in cond_order if c in brand_cond.columns] + [c for c in brand_cond.columns if c not in cond_order]]
    brand_sub = pivot("subcategory")
    models = None
    if "model" in df and df["model"].notna().any():
        models = (df.dropna(subset=["model"]).groupby(["brand", "model"])
                  .agg(skus=("id", "size"), median_price_cny=("price_cny", "median"),
                       median_retail_cny=("retail_cny", "median"), median_discount_pct=("discount_pct", "median"))
                  .reset_index().sort_values("skus", ascending=False))
    return brand, cond, sub, disc_bands, brand_cond, brand_sub, pivot("gender"), pivot("color"), pivot("location"), models


def export_dashboard(df, brand, cond, sub, bands, brand_cond, models, deepest, con, d):
    """Small CSVs the dashboard reads (each well under 8 MB)."""
    d.mkdir(exist_ok=True)
    r = lambda x: x.round(1)
    kpi = pd.DataFrame([{
        "snapshot_date": df["scraped_at"].iloc[0][:10], "handbags": len(df), "brands": df["brand"].nunique(),
        "median_price_cny": df["price_cny"].median(), "median_retail_cny": df["retail_cny"].median(),
        "median_discount_pct": df["discount_pct"].median(),
        "inventory_value_cny": df["price_cny"].sum(), "retail_value_cny": df["retail_cny"].sum(),
        "listed_last_7d": int((df["days_listed"] <= 7).sum()),
        "share_over_70_off_pct": (df["discount_pct"] >= 70).mean() * 100,
    }])
    r(kpi).to_csv(d / "kpis.csv", index=False)
    b = brand.reset_index()
    top_sub = (df.groupby(["brand", "subcategory"]).size().reset_index(name="n")
               .sort_values("n", ascending=False).drop_duplicates("brand").set_index("brand")["subcategory"])
    b["top_type"] = b["brand"].map(top_sub)
    r(b[["rank", "brand", "skus", "share_pct", "median_price_cny", "median_retail_cny", "median_discount_pct",
         "p25_discount_pct", "p75_discount_pct", "inventory_value_cny", "median_days_listed", "listed_last_7d", "top_type"]]
      ).to_csv(d / "brands.csv", index=False)
    r(cond.reset_index()).to_csv(d / "condition.csv", index=False)
    r(sub.reset_index()).to_csv(d / "types.csv", index=False)
    bands.reset_index().rename(columns={"discount_pct": "band"}).to_csv(d / "discount_bands.csv", index=False)
    bc = brand_cond.head(15).reset_index().melt(id_vars="brand", var_name="condition", value_name="skus")
    bc.to_csv(d / "brand_condition.csv", index=False)
    if "location" in df:
        loc = df["location"].fillna("(unknown)").str.split(r" \| ").explode().value_counts().rename_axis("location").reset_index(name="skus")
        loc.to_csv(d / "locations.csv", index=False)
    cols = ["id", "brand", "name_zh", "model", "subcategory", "condition", "price_cny", "retail_cny", "discount_pct", "listed_at", "url"]
    r(deepest.head(300)[[c for c in cols if c in deepest]]).to_csv(d / "deals.csv", index=False)
    if models is not None:
        r(models.head(300)).to_csv(d / "models.csv", index=False)
    hist = pd.read_sql("""SELECT snapshot_date, COUNT(*) AS handbags, AVG(discount_pct) AS mean_discount_pct,
                          SUM(price_cny) AS inventory_value_cny FROM price_history GROUP BY 1 ORDER BY 1""", con)
    r(hist).to_csv(d / "history.csv", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args()
    date = a.date or latest_date()
    out = ROOT / "reports" / date
    out.mkdir(parents=True, exist_ok=True)

    df = merge_details(build_listings(date))
    facets = json.loads((SNAP / date / "facets.json").read_text(encoding="utf-8"))
    con = update_db(df, facets, date)
    changes = price_changes(con, date)
    brand, cond, sub, bands, brand_cond, brand_sub, brand_gender, brand_color, brand_loc, models = summaries(df)

    df.drop(columns=[c for c in ["subcategories"] if c in df]).to_csv(out / "handbags.csv", index=False, encoding="utf-8-sig")
    brand.to_csv(out / "brand_summary.csv", encoding="utf-8-sig")

    deepest = df[df["retail_cny"] >= 3000].sort_values("discount_pct", ascending=False).head(500)
    xlsx = out / f"zzer_handbags_{date}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        brand.to_excel(xw, sheet_name="Brand summary")
        brand_cond.to_excel(xw, sheet_name="Brand x condition")
        if not brand_sub.empty: brand_sub.to_excel(xw, sheet_name="Brand x type")
        if not brand_gender.empty: brand_gender.to_excel(xw, sheet_name="Brand x gender")
        if not brand_color.empty: brand_color.to_excel(xw, sheet_name="Brand x color")
        if not brand_loc.empty: brand_loc.to_excel(xw, sheet_name="Brand x location")
        if models is not None: models.to_excel(xw, sheet_name="Models", index=False)
        cond.to_excel(xw, sheet_name="By condition")
        sub.to_excel(xw, sheet_name="By type")
        bands.to_excel(xw, sheet_name="Discount bands")
        deepest.to_excel(xw, sheet_name="Deepest discounts", index=False)
        if changes:
            changes["changed"].merge(df[["id", "brand", "name_zh", "url"]], on="id").sort_values("change_pct").to_excel(xw, sheet_name="Price changes", index=False)
        df.drop(columns=["subcategories"], errors="ignore").to_excel(xw, sheet_name="All handbags", index=False)
    # basic formatting: freeze header, widen columns
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    for ws in wb.worksheets:
        ws.freeze_panes = "B2"
        for col in ws.columns:
            width = min(max(len(str(c.value or "")) for c in list(col)[:200]) + 2, 50)
            ws.column_dimensions[col[0].column_letter].width = width
    wb.save(xlsx)

    top = brand.head(40)
    summary = {
        "date": date, "generated_at": datetime.now(timezone.utc).isoformat(),
        "site_total": facets.get("total"), "scraped": int(len(df)),
        "brands": int(df["brand"].nunique()),
        "median_price_cny": float(df["price_cny"].median()), "median_retail_cny": float(df["retail_cny"].median()),
        "median_discount_pct": float(df["discount_pct"].median()), "mean_discount_pct": float(df["discount_pct"].mean()),
        "inventory_value_cny": float(df["price_cny"].sum()), "retail_value_cny": float(df["retail_cny"].sum()),
        "listed_last_7d": int((df["days_listed"] <= 7).sum()),
        "with_details": int(df["details_fetched_at"].notna().sum()) if "details_fetched_at" in df else 0,
        "brand_table": top.reset_index().round(1).to_dict(orient="records"),
        "condition": cond.reset_index().round(1).to_dict(orient="records"),
        "subcategory": sub.reset_index().round(1).to_dict(orient="records"),
        "discount_bands": [{"band": str(k), "skus": int(v)} for k, v in bands["skus"].items()],
        "brand_condition": {"brands": list(brand_cond.head(15).index), "conditions": list(brand_cond.columns),
                            "values": brand_cond.head(15).values.tolist()},
        "location": (df["location"].fillna("(unknown)").str.split(r" \| ").explode().value_counts().to_dict() if "location" in df else {}),
        "deepest": deepest.head(25)[["brand", "name_zh", "condition", "price_cny", "retail_cny", "discount_pct", "url", "image_url"]].round(1).to_dict(orient="records"),
        "models": models.head(30).round(1).to_dict(orient="records") if models is not None else [],
        "changes": None if not changes else {"previous": changes["previous"], "price_changes": len(changes["changed"]),
                                             "price_drops": int((changes["changed"]["change_cny"] < 0).sum()),
                                             "new": len(changes["new"]), "gone": len(changes["gone"])},
    }
    export_dashboard(df, brand, cond, sub, bands, brand_cond, models, deepest, con, out / "dashboard")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"{len(df)} handbags, {summary['brands']} brands -> {out}")
    print(brand.head(15)[["skus", "median_price_cny", "median_retail_cny", "median_discount_pct"]].round(1).to_string())


if __name__ == "__main__":
    main()
