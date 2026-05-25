# ==========================
# IMPORTS
# ==========================
import os
import io
import sqlite3
from typing import List, Dict

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

st.set_page_config(page_title=APP_TITLE, layout="wide")

# ==========================
# DB HELPERS
# ==========================
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def init_db():
    """Initialize DB tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS domains(
            id INTEGER PRIMARY KEY,
            domain TEXT UNIQUE,
            account_manager TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS partners(
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            integration_type TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS partner_lines(
            id INTEGER PRIMARY KEY,
            partner_id INTEGER,
            line TEXT
        )
    """)
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
# WRITE HELPERS — DOMAINS
# ==========================
def add_domain(d, am):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO domains(domain,account_manager) VALUES (?,?)", (d, am))
    conn.execute("UPDATE domains SET account_manager=? WHERE domain=?", (am, d))
    conn.commit()
    conn.close()
    get_domains.clear()


# ==========================
# WRITE HELPERS — PARTNERS
# ==========================
def add_partner(name: str, itype: str, lines_raw: str):
    """Insert a new partner with their lines."""
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO partners(name,integration_type) VALUES (?,?)", (name, itype))
    pid = conn.execute("SELECT id FROM partners WHERE name=?", (name,)).fetchone()[0]
    for ln in lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            conn.execute("INSERT INTO partner_lines(partner_id,line) VALUES (?,?)", (pid, ln))
    conn.commit()
    conn.close()
    get_partners.clear()
    get_partner_lines.clear()


def update_partner(pid: int, new_name: str, new_itype: str, lines_raw: str):
    """Update partner name, integration type, and replace all lines."""
    conn = get_conn()
    conn.execute(
        "UPDATE partners SET name=?, integration_type=? WHERE id=?",
        (new_name, new_itype, pid)
    )
    # Replace all lines
    conn.execute("DELETE FROM partner_lines WHERE partner_id=?", (pid,))
    for ln in lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            conn.execute("INSERT INTO partner_lines(partner_id,line) VALUES (?,?)", (pid, ln))
    conn.commit()
    conn.close()
    get_partners.clear()
    get_partner_lines.clear()


def delete_partner(pid: int):
    """Delete a partner and all their lines."""
    conn = get_conn()
    conn.execute("DELETE FROM partner_lines WHERE partner_id=?", (pid,))
    conn.execute("DELETE FROM partners WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    get_partners.clear()
    get_partner_lines.clear()


# ==========================
# CRAWLER
# ==========================
def fetch_ads_txt(domain: str):
    urls = [f"https://{domain}/ads.txt", f"http://{domain}/ads.txt"]
    for url in urls:
        try:
            r = requests.get(url, timeout=8, allow_redirects=True)
            if r.status_code == 200:
                lines = [
                    line.strip().lower()
                    for line in r.text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if lines:
                    return lines, "crawler"
        except requests.exceptions.RequestException:
            continue
    return [], "blocked"


def parse_manual_lines(raw: str) -> List[str]:
    return [
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def norm(x: str) -> str:
    return "".join(x.split()).lower()


# ==========================
# INIT DB
# ==========================
init_db()

# ==========================
# UI — HEADER
# ==========================
st.title(f"🔍 {APP_TITLE}")
st.caption("Validate ads.txt coverage across domains and demand partners.")

# ==========================
# TABS
# ==========================
tab_validate, tab_partners = st.tabs(["✅ Validate", "🤝 Manage Partners"])


# ============================================================
# TAB 2 — PARTNER MANAGEMENT (full CRUD)
# ============================================================
with tab_partners:
    st.header("🤝 Manage Demand Partners")

    partners = get_partners()
    partner_map = {p[1]: p for p in partners}

    # ── ADD NEW PARTNER ─────────────────────────────────────
    with st.expander("➕ Add New Partner", expanded=len(partners) == 0):
        np_name = st.text_input("Partner Name", key="np_name")
        np_itype = st.text_input("Integration Type (e.g. Direct, Reseller)", key="np_itype")
        np_lines = st.text_area(
            "Ads.txt Lines (paste all, one per line)",
            height=200,
            key="np_lines",
            placeholder="pubmatic.com, 123456, DIRECT, abc123\nappnexus.com, 789, RESELLER"
        )
        if st.button("✅ Add Partner", type="primary", key="btn_add_partner"):
            if not np_name.strip():
                st.warning("Partner name is required.")
            elif np_name.strip() in partner_map:
                st.error(f"Partner **{np_name.strip()}** already exists.")
            elif not np_lines.strip():
                st.warning("Please paste at least one ads.txt line.")
            else:
                add_partner(np_name.strip(), np_itype.strip(), np_lines.strip())
                st.success(f"✅ Partner **{np_name.strip()}** added!")
                st.rerun()

    st.divider()

    # ── LIST / EDIT / DELETE PARTNERS ───────────────────────
    if not partners:
        st.info("No partners yet. Add your first partner above.")
    else:
        st.subheader(f"📋 All Partners ({len(partners)})")

        # Search/filter
        search = st.text_input("🔍 Search partners", placeholder="Type to filter...", key="partner_search")
        filtered = [p for p in partners if search.lower() in p[1].lower()] if search else partners

        for pid, pname, pitype in filtered:
            lines = get_partner_lines(pid)
            with st.expander(f"**{pname}** — {pitype or 'N/A'} — {len(lines)} line(s)"):

                col_info, col_actions = st.columns([3, 1])

                with col_info:
                    # Edit form inside expander
                    edit_name = st.text_input("Partner Name", value=pname, key=f"edit_name_{pid}")
                    edit_itype = st.text_input("Integration Type", value=pitype or "", key=f"edit_itype_{pid}")
                    edit_lines = st.text_area(
                        "Ads.txt Lines (one per line)",
                        value="\n".join(lines),
                        height=200,
                        key=f"edit_lines_{pid}"
                    )

                with col_actions:
                    st.markdown("&nbsp;", unsafe_allow_html=True)  # spacing
                    st.markdown("&nbsp;", unsafe_allow_html=True)

                    if st.button("💾 Save", key=f"save_{pid}", use_container_width=True):
                        if not edit_name.strip():
                            st.warning("Name cannot be empty.")
                        elif not edit_lines.strip():
                            st.warning("Lines cannot be empty.")
                        else:
                            update_partner(pid, edit_name.strip(), edit_itype.strip(), edit_lines.strip())
                            st.success(f"✅ **{edit_name.strip()}** updated!")
                            st.rerun()

                    st.markdown("---")

                    if st.button("🗑️ Delete", key=f"del_{pid}", use_container_width=True):
                        st.session_state[f"confirm_del_{pid}"] = True

                    if st.session_state.get(f"confirm_del_{pid}"):
                        st.error(f"Delete **{pname}**?")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Yes", key=f"yes_del_{pid}", use_container_width=True):
                                delete_partner(pid)
                                st.session_state.pop(f"confirm_del_{pid}", None)
                                st.success(f"Deleted **{pname}**")
                                st.rerun()
                        with c2:
                            if st.button("No", key=f"no_del_{pid}", use_container_width=True):
                                st.session_state.pop(f"confirm_del_{pid}", None)
                                st.rerun()

# ============================================================
# TAB 1 — VALIDATE
# ============================================================
with tab_validate:

    # Reload partners fresh for validation tab
    partners = get_partners()
    partner_map = {p[1]: p for p in partners}
    domains, am_map = get_domains()

    # ==========================
    # SIDEBAR
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

    if not partners:
        st.warning("⚠️ No partners found. Go to the **Manage Partners** tab to add your first partner.")
        st.stop()

    # ==========================
    # DOMAIN SELECTION
    # ==========================
    col_left, col_right = st.columns([2, 1])

    with col_left:
        selected_domains = st.multiselect(
            "Select Domains from DB",
            domains,
            help="Pick one or more domains to validate."
        )

    with col_right:
        pasted_domains = st.text_area(
            "Or paste domains (one per line / space-separated)",
            height=120,
            help="Extra domains not in the DB will be added automatically."
        )

    # ==========================
    # PARTNER SELECTION
    # ==========================
    selected_partners = st.multiselect(
        "Select Partners to Validate",
        list(partner_map.keys()),
        default=list(partner_map.keys()),
        help="Choose which demand partners to check coverage for."
    )

    show_missing_lines = st.checkbox("Show missing lines in results", value=False)

    st.divider()

    # ==========================
    # MANUAL ADS.TXT INPUT
    # ==========================
    st.subheader("📋 Manual Ads.txt Input (for blocked sites)")
    st.caption(
        "If a site blocks the auto-crawler, open **site.com/ads.txt** in your browser, "
        "copy all lines, and paste them below. Manual input takes priority over the crawler."
    )

    preview_doms = set(selected_domains)
    for d in pasted_domains.replace(",", " ").split():
        d = d.strip().lower().rstrip("/")
        if d:
            preview_doms.add(d)

    manual_inputs: Dict[str, str] = {}

    if preview_doms:
        cols = st.columns(2)
        for i, d in enumerate(sorted(preview_doms)):
            with cols[i % 2]:
                manual_inputs[d] = st.text_area(
                    f"📌 {d}",
                    height=150,
                    key=f"manual_{d}",
                    placeholder=f"Paste content from https://{d}/ads.txt here (optional)...",
                    help=f"Leave empty to use auto-crawler."
                )
    else:
        st.info("ℹ️ Select or paste domains above — manual input boxes will appear here.")

    st.divider()

    # ==========================
    # VALIDATE BUTTON
    # ==========================
    if st.button("🚀 Validate", type="primary"):

        doms = set(selected_domains)
        for d in pasted_domains.replace(",", " ").split():
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
        crawler_status: Dict[str, str] = {}

        progress = st.progress(0, text="Fetching ads.txt files...")
        total = len(doms)

        for i, d in enumerate(sorted(doms)):
            progress.progress(i / total, text=f"Processing `{d}`...")

            manual_raw = manual_inputs.get(d, "").strip()

            if manual_raw:
                live = parse_manual_lines(manual_raw)
                crawler_status[d] = "manual"
            else:
                live, status = fetch_ads_txt(d)
                crawler_status[d] = status

            live_norm = set(norm(x) for x in live)

            for p in selected_partners:
                pid, name, itype = partner_map[p]
                lines = get_partner_lines(pid)

                primary_line = lines[0] if lines else None
                primary_present = (
                    "✅ Yes" if primary_line and norm(primary_line) in live_norm
                    else "❌ No"
                )

                present = [l for l in lines if norm(l) in live_norm]
                missing = [l for l in lines if norm(l) not in live_norm]

                total_lines = len(lines)
                coverage_pct = round((len(present) / total_lines * 100), 1) if total_lines > 0 else 0.0

                source_label = (
                    "🖐 Manual" if crawler_status[d] == "manual"
                    else ("🤖 Crawler" if crawler_status[d] == "crawler" else "⚠️ Blocked")
                )

                results.append({
                    "Domain": d,
                    "Account Manager": am_map.get(d, ""),
                    "Source": source_label,
                    "Partner": name,
                    "Integration": itype,
                    "Primary Line Present": primary_present,
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
        # CRAWLER STATUS BANNER
        # ==========================
        blocked = [d for d, s in crawler_status.items() if s == "blocked"]
        manual_used = [d for d, s in crawler_status.items() if s == "manual"]
        crawled = [d for d, s in crawler_status.items() if s == "crawler"]

        if blocked:
            st.warning(
                f"⚠️ **Crawler was blocked for {len(blocked)} domain(s):** "
                f"`{'`, `'.join(blocked)}`  \n"
                "Paste their ads.txt content in the manual boxes above and re-validate."
            )
        if manual_used:
            st.info(f"🖐 **Manual input used for:** `{'`, `'.join(manual_used)}`")
        if crawled:
            st.success(f"🤖 **Crawler succeeded for:** `{'`, `'.join(crawled)}`")

        # ==========================
        # SUMMARY METRICS
        # ==========================
        st.subheader("📈 Summary")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Domains Checked", len(doms))
        m2.metric("Partners Checked", len(selected_partners))
        m3.metric("Avg Coverage %", f"{df['Coverage %'].mean():.1f}%")
        fully_covered = df[df["Missing"] == 0].shape[0]
        m4.metric("Fully Covered", fully_covered)
        primary_ok = df[df["Primary Line Present"] == "✅ Yes"].shape[0]
        m5.metric("Primary Lines OK", f"{primary_ok}/{len(df)}")

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

        def highlight_primary(val):
            if val == "✅ Yes":
                return "background-color: #d4edda; color: #155724"
            elif val == "❌ No":
                return "background-color: #f8d7da; color: #721c24"
            return ""

        styled = (
            df.style
            .map(highlight_coverage, subset=["Coverage %"])
            .map(highlight_primary, subset=["Primary Line Present"])
        )
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
                            missing_rows.append({
                                "Domain": domain,
                                "Partner": partner_name,
                                "Missing Line": line
                            })
                if missing_rows:
                    pd.DataFrame(missing_rows).to_excel(writer, index=False, sheet_name="Missing Lines")

        excel_buf.seek(0)
        st.download_button(
            label="⬇️ Download Excel Report",
            data=excel_buf,
            file_name="ads_txt_validation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
