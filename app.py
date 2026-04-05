# ==========================
# IMPORTS
# ==========================
import os
import io
import sqlite3
from typing import List, Dict, Optional

import pandas as pd
import requests
import streamlit as st

# ==========================
# CONFIG
# ==========================
APP_TITLE = "V-Ads.txt-validator"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DB_DIR, exist_ok=True)

DB_FILE = os.path.join(DB_DIR, "adsdata.db")
EXCEL_FILE = os.path.join(DB_DIR, "App-Demand Ops Ads.txt coverage (2).xlsx")

st.set_page_config(page_title=APP_TITLE, layout="wide")

# ==========================
# DB HELPERS
# ==========================
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def init_db_from_excel():
    if not os.path.exists(EXCEL_FILE):
        st.error(
            "❌ Excel file not found! Please place your Excel file at:\n\n"
            f"`{EXCEL_FILE}`"
        )
        st.info(
            "Expected filename: `App-Demand Ops Ads.txt coverage (2).xlsx`\n\n"
            "Place it inside the `database/` folder next to `app.py`."
        )
        st.stop()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("CREATE TABLE IF NOT EXISTS domains(id INTEGER PRIMARY KEY, domain TEXT UNIQUE, account_manager TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS partners(id INTEGER PRIMARY KEY, name TEXT UNIQUE, integration_type TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS partner_lines(id INTEGER PRIMARY KEY, partner_id INTEGER, line TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS master_lines(id INTEGER PRIMARY KEY, line TEXT UNIQUE)")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM domains")
    if cur.fetchone()[0] > 0:
        conn.close()
        return

    xls = pd.ExcelFile(EXCEL_FILE)
    sheets = xls.sheet_names

    master_df = xls.parse(sheets[0])

    for dom, am in zip(master_df.iloc[:, 0], master_df.iloc[:, 3]):
        if pd.notna(dom):
            cur.execute(
                "INSERT OR IGNORE INTO domains(domain, account_manager) VALUES (?,?)",
                (str(dom).strip().lower(), str(am) if pd.notna(am) else "")
            )

    for val in master_df.iloc[:, 6].dropna():
        for ln in str(val).split("\n"):
            ln = ln.strip().lower()
            if ln:
                cur.execute("INSERT OR IGNORE INTO master_lines(line) VALUES (?)", (ln,))

    for sheet in sheets[1:-1]:
        df = xls.parse(sheet)
        pname = sheet.strip()

        integration = ""
        if df.shape[1] > 4:
            col = df.iloc[:, 4].dropna()
            if not col.empty:
                integration = str(col.iloc[0])

        cur.execute("INSERT OR IGNORE INTO partners(name, integration_type) VALUES (?,?)", (pname, integration))
        cur.execute("SELECT id FROM partners WHERE name=?", (pname,))
        pid = cur.fetchone()[0]

        for val in df.iloc[:, 6].dropna():
            for ln in str(val).split("\n"):
                ln = ln.strip().lower()
                if ln:
                    cur.execute("INSERT INTO partner_lines(partner_id,line) VALUES (?,?)", (pid, ln))
                    cur.execute("INSERT OR IGNORE INTO master_lines(line) VALUES (?)", (ln,))

    conn.commit()
    conn.close()


# ==========================
# CACHED DB READS
# ==========================
@st.cache_data(ttl=300)
def get_domains():
    conn = get_conn()
    rows = conn.execute("SELECT domain, account_manager FROM domains ORDER BY domain").fetchall()
    conn.close()
    return [r[0] for r in rows], {r[0]: r[1] for r in rows}


@st.cache_data(ttl=300)
def get_partners():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, integration_type FROM partners ORDER BY name").fetchall()
    conn.close()
    return rows


@st.cache_data(ttl=300)
def get_partner_lines(pid):
    conn = get_conn()
    rows = conn.execute("SELECT line FROM partner_lines WHERE partner_id=?", (pid,)).fetchall()
    conn.close()
    return list(dict.fromkeys([r[0] for r in rows]))


# ==========================
# WRITE HELPERS (invalidate cache after write)
# ==========================
def add_domain(d, am):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO domains(domain,account_manager) VALUES (?,?)", (d, am))
    conn.execute("UPDATE domains SET account_manager=? WHERE domain=?", (am, d))
    conn.commit()
    conn.close()
    get_domains.clear()


def add_partner(name, itype, lines):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO partners(name,integration_type) VALUES (?,?)", (name, itype))
    pid = conn.execute("SELECT id FROM partners WHERE name=?", (name,)).fetchone()[0]

    for ln in lines:
        ln = ln.strip().lower()
        if ln:
            conn.execute("INSERT INTO partner_lines(partner_id,line) VALUES (?,?)", (pid, ln))
            conn.execute("INSERT OR IGNORE INTO master_lines(line) VALUES (?)", (ln,))

    conn.commit()
    conn.close()
    get_partners.clear()
    get_partner_lines.clear()


# ==========================
# CRAWLER
# ==========================
def fetch_ads_txt(domain: str) -> List[str]:
    """Fetch and return non-comment lines from a domain's ads.txt."""
    urls = [f"https://{domain}/ads.txt", f"http://{domain}/ads.txt"]
    for url in urls:
        try:
            r = requests.get(url, timeout=8, allow_redirects=True)
            if r.status_code == 200:
                return [
                    line.strip().lower()
                    for line in r.text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
        except requests.exceptions.RequestException:
            continue
    return []


def norm(x: str) -> str:
    """Normalize a line for comparison (strip whitespace, lowercase)."""
    return "".join(x.split()).lower()


# ==========================
# INIT DB
# ==========================
if not os.path.exists(DB_FILE):
    init_db_from_excel()

domains, am_map = get_domains()
partners = get_partners()
partner_map = {p[1]: p for p in partners}

# ==========================
# UI — HEADER
# ==========================
st.title(f"🔍 {APP_TITLE}")
st.caption("Validate ads.txt coverage across domains and demand partners.")

# ==========================
# SIDEBAR — INFO & CONTROLS
# ==========================
with st.sidebar:
    st.header("📊 Database Info")
    st.metric("Domains", len(domains))
    st.metric("Partners", len(partners))

    st.divider()

    st.subheader("➕ Add New Domain")
    new_domain = st.text_input("Domain (e.g. example.com)")
    new_am = st.text_input("Account Manager")
    if st.button("Add Domain"):
        if new_domain:
            add_domain(new_domain.strip().lower(), new_am.strip())
            st.success(f"Added: {new_domain}")
            st.rerun()
        else:
            st.warning("Please enter a domain.")

    st.divider()
    st.caption("DB path: `database/adsdata.db`")
    st.caption("Excel: `database/App-Demand Ops Ads.txt coverage (2).xlsx`")

# ==========================
# MAIN — VALIDATION
# ==========================
col_left, col_right = st.columns([2, 1])

with col_left:
    selected_domains = st.multiselect(
        "Select Domains from DB",
        domains,
        help="Pick one or more domains to validate."
    )

with col_right:
    pasted = st.text_area(
        "Or paste domains (one per line / space-separated)",
        height=120,
        help="Extra domains not in the DB will be added automatically."
    )

selected_partners = st.multiselect(
    "Select Partners to Validate",
    list(partner_map.keys()),
    default=list(partner_map.keys()),
    help="Choose which demand partners to check coverage for."
)

show_missing_lines = st.checkbox("Show missing lines in results", value=False)

st.divider()

if st.button("🚀 Validate", type="primary"):

    # Collect all domains
    doms = set(selected_domains)
    for d in pasted.replace(",", " ").split():
        d = d.strip().lower().rstrip("/")
        if d:
            doms.add(d)
            if d not in domains:
                add_domain(d, "")

    if not doms:
        st.warning("Please select or paste at least one domain.")
        st.stop()

    if not selected_partners:
        st.warning("Please select at least one partner.")
        st.stop()

    results = []
    missing_detail: Dict[str, Dict[str, List[str]]] = {}

    progress = st.progress(0, text="Fetching ads.txt files...")
    total = len(doms)

    for i, d in enumerate(sorted(doms)):
        progress.progress((i) / total, text=f"Fetching ads.txt for `{d}`...")

        live = fetch_ads_txt(d)
        live_norm = set(norm(x) for x in live)

        for p in selected_partners:
            pid, name, itype = partner_map[p]
            lines = get_partner_lines(pid)

            present = [l for l in lines if norm(l) in live_norm]
            missing = [l for l in lines if norm(l) not in live_norm]

            total_lines = len(lines)
            coverage_pct = round((len(present) / total_lines * 100), 1) if total_lines > 0 else 0.0

            results.append({
                "Domain": d,
                "Account Manager": am_map.get(d, ""),
                "Partner": name,
                "Integration": itype,
                "Total Lines": total_lines,
                "Present": len(present),
                "Missing": len(missing),
                "Coverage %": coverage_pct,
            })

            if missing and show_missing_lines:
                missing_detail.setdefault(d, {})[name] = missing

    progress.progress(1.0, text="✅ Done!")

    df = pd.DataFrame(results)

    # ==========================
    # SUMMARY METRICS
    # ==========================
    st.subheader("📈 Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Domains Checked", len(doms))
    m2.metric("Partners Checked", len(selected_partners))
    m3.metric("Avg Coverage %", f"{df['Coverage %'].mean():.1f}%")
    fully_covered = df[df["Missing"] == 0].shape[0]
    m4.metric("Fully Covered (Partner×Domain)", fully_covered)

    st.divider()

    # ==========================
    # RESULTS TABLE
    # ==========================
    st.subheader("📋 Results")

    def highlight_coverage(val):
        if isinstance(val, float):
            if val == 100:
                return "background-color: #d4edda; color: #155724"
            elif val >= 50:
                return "background-color: #fff3cd; color: #856404"
            else:
                return "background-color: #f8d7da; color: #721c24"
        return ""

    styled = df.style.map(highlight_coverage, subset=["Coverage %"])
    st.dataframe(styled, use_container_width=True)

    # ==========================
    # MISSING LINES DETAIL
    # ==========================
    if show_missing_lines and missing_detail:
        st.subheader("🔎 Missing Lines Detail")
        for domain, partners_info in missing_detail.items():
            with st.expander(f"🌐 {domain}"):
                for partner_name, lines in partners_info.items():
                    st.markdown(f"**{partner_name}** — {len(lines)} missing line(s):")
                    st.code("\n".join(lines), language="text")

    # ==========================
    # DOWNLOAD
    # ==========================
    st.divider()
    excel_buf = io.BytesIO()
    with pd.ExcelWriter(excel_buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Validation Results")

        if show_missing_lines and missing_detail:
            missing_rows = []
            for domain, partners_info in missing_detail.items():
                for partner_name, lines in partners_info.items():
                    for line in lines:
                        missing_rows.append({"Domain": domain, "Partner": partner_name, "Missing Line": line})
            if missing_rows:
                pd.DataFrame(missing_rows).to_excel(writer, index=False, sheet_name="Missing Lines")

    excel_buf.seek(0)
    st.download_button(
        label="⬇️ Download Excel Report",
        data=excel_buf,
        file_name="ads_txt_validation.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
