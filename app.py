# ==========================
# IMPORTS
# ==========================
import io
import os
from typing import List, Dict

import pandas as pd
import requests
import streamlit as st
from supabase import create_client, Client

# ==========================
# CONFIG
# ==========================
APP_TITLE = "Demand-Ads.txt-validator"
INTEGRATION_OPTIONS = ["VAST", "PREBID", "VAST+PREBID", "ORTB", "Custom..."]

SUPABASE_URL = "https://sfupddaemxpalstlomzt.supabase.co"
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

st.set_page_config(page_title=APP_TITLE, layout="wide")

# ==========================
# SUPABASE CLIENT
# ==========================
@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ==========================
# CACHED READS
# ==========================
@st.cache_data(ttl=60)
def get_domains():
    sb = get_supabase()
    rows = sb.table("domains").select("domain, account_manager").order("domain").execute().data
    domain_list = [r["domain"] for r in rows]
    am_map = {r["domain"]: r["account_manager"] or "" for r in rows}
    return domain_list, am_map


@st.cache_data(ttl=60)
def get_partners():
    sb = get_supabase()
    rows = sb.table("partners").select("id, name, integration_type").order("name").execute().data
    return [(r["id"], r["name"], r["integration_type"] or "") for r in rows]


@st.cache_data(ttl=60)
def get_partner_lines(pid):
    sb = get_supabase()
    rows = sb.table("partner_lines").select("line").eq("partner_id", pid).eq("is_primary", False).execute().data
    return list(dict.fromkeys([r["line"] for r in rows]))


@st.cache_data(ttl=60)
def get_partner_primary_lines(pid):
    sb = get_supabase()
    rows = sb.table("partner_lines").select("line").eq("partner_id", pid).eq("is_primary", True).execute().data
    return list(dict.fromkeys([r["line"] for r in rows]))


# ==========================
# WRITE HELPERS — DOMAINS
# ==========================
def add_domain(d: str, am: str):
    sb = get_supabase()
    sb.table("domains").upsert({"domain": d, "account_manager": am}, on_conflict="domain").execute()
    get_domains.clear()


# ==========================
# WRITE HELPERS — PARTNERS
# ==========================
def add_partner(name: str, itype: str, lines_raw: str, primary_lines_raw: str):
    sb = get_supabase()
    sb.table("partners").upsert({"name": name, "integration_type": itype}, on_conflict="name").execute()
    pid = sb.table("partners").select("id").eq("name", name).execute().data[0]["id"]
    rows = []
    for ln in lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            rows.append({"partner_id": pid, "line": ln, "is_primary": False})
    for ln in primary_lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            rows.append({"partner_id": pid, "line": ln, "is_primary": True})
    if rows:
        sb.table("partner_lines").insert(rows).execute()
    get_partners.clear()
    get_partner_lines.clear()
    get_partner_primary_lines.clear()


def update_partner(pid: int, new_name: str, new_itype: str, lines_raw: str, primary_lines_raw: str):
    sb = get_supabase()
    sb.table("partners").update({"name": new_name, "integration_type": new_itype}).eq("id", pid).execute()
    sb.table("partner_lines").delete().eq("partner_id", pid).execute()
    rows = []
    for ln in lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            rows.append({"partner_id": pid, "line": ln, "is_primary": False})
    for ln in primary_lines_raw.splitlines():
        ln = ln.strip().lower()
        if ln:
            rows.append({"partner_id": pid, "line": ln, "is_primary": True})
    if rows:
        sb.table("partner_lines").insert(rows).execute()
    get_partners.clear()
    get_partner_lines.clear()
    get_partner_primary_lines.clear()


def delete_partner(pid: int):
    sb = get_supabase()
    sb.table("partner_lines").delete().eq("partner_id", pid).execute()
    sb.table("partners").delete().eq("id", pid).execute()
    get_partners.clear()
    get_partner_lines.clear()
    get_partner_primary_lines.clear()


# ==========================
# HELPER — Integration type widget
# ==========================
def integration_type_widget(key_prefix: str, current_value: str = ""):
    if current_value and current_value not in ["VAST", "PREBID", "VAST+PREBID", "ORTB", ""]:
        default_idx = INTEGRATION_OPTIONS.index("Custom...")
    else:
        opts_map = {"VAST": 0, "PREBID": 1, "VAST+PREBID": 2, "ORTB": 3, "": 0}
        default_idx = opts_map.get(current_value, 0)
    selected = st.selectbox("Integration Type", INTEGRATION_OPTIONS, index=default_idx, key=f"{key_prefix}_itype_select")
    if selected == "Custom...":
        custom = st.text_input(
            "Custom Integration Type",
            value=current_value if current_value not in ["VAST", "PREBID", "VAST+PREBID", "ORTB"] else "",
            key=f"{key_prefix}_itype_custom"
        )
        return custom.strip()
    return selected


# ==========================
# HELPER — Build primary lines .txt
# ==========================
def build_primary_txt(partner_ids_names) -> str:
    sections = []
    for pid, pname in partner_ids_names:
        plines = get_partner_primary_lines(pid)
        if plines:
            block = f"# {pname}\n" + "\n".join(plines)
            sections.append(block)
    return "\n\n".join(sections)


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
# UI — HEADER
# ==========================
st.title(APP_TITLE)
st.caption("Validate ads.txt coverage across domains and demand partners.")

# ==========================
# TABS
# ==========================
tab_validate, tab_partners, tab_export = st.tabs(["Validate", "Manage Partners", "Export Lines"])


# ============================================================
# TAB 2 — PARTNER MANAGEMENT
# ============================================================
with tab_partners:
    st.header("Manage Demand Partners")
    partners = get_partners()
    partner_map = {p[1]: p for p in partners}

    with st.expander("Add New Partner", expanded=len(partners) == 0):
        np_name = st.text_input("Partner Name", key="np_name")
        np_itype = integration_type_widget("np")
        col_lines, col_primary = st.columns(2)
        with col_lines:
            np_lines = st.text_area("All Ads.txt Lines (one per line)", height=220, key="np_lines",
                placeholder="pubmatic.com, 123456, DIRECT, abc123\nappnexus.com, 789, RESELLER\n...")
        with col_primary:
            np_primary = st.text_area("Primary Lines (subset — for approvals & publisher output)", height=220,
                key="np_primary", placeholder="pubmatic.com, 123456, DIRECT, abc123\n...")
        if st.button("Add Partner", type="primary", key="btn_add_partner"):
            if not np_name.strip():
                st.warning("Partner name is required.")
            elif np_name.strip() in partner_map:
                st.error(f"Partner **{np_name.strip()}** already exists.")
            elif not np_lines.strip():
                st.warning("Please paste at least one ads.txt line.")
            else:
                add_partner(np_name.strip(), np_itype, np_lines.strip(), np_primary.strip())
                st.success(f"Partner **{np_name.strip()}** added!")
                st.rerun()

    st.divider()

    if partners:
        primary_txt = build_primary_txt([(p[0], p[1]) for p in partners])
        st.download_button(
            label="Download Primary Lines .txt (all partners)",
            data=primary_txt if primary_txt else "# No primary lines found",
            file_name="primary_lines.txt", mime="text/plain"
        )

    st.divider()

    if not partners:
        st.info("No partners yet. Add your first partner above.")
    else:
        st.subheader(f"All Partners ({len(partners)})")
        search = st.text_input("Search partners", placeholder="Type to filter...", key="partner_search")
        filtered = [p for p in partners if search.lower() in p[1].lower()] if search else partners

        for pid, pname, pitype in filtered:
            lines = get_partner_lines(pid)
            primary_lines = get_partner_primary_lines(pid)
            with st.expander(f"**{pname}** — {pitype or 'N/A'} — {len(lines)} line(s) | {len(primary_lines)} primary"):
                col_info, col_actions = st.columns([3, 1])
                with col_info:
                    edit_name = st.text_input("Partner Name", value=pname, key=f"edit_name_{pid}")
                    edit_itype = integration_type_widget(f"edit_{pid}", current_value=pitype or "")
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        edit_lines = st.text_area("All Ads.txt Lines", value="\n".join(lines), height=220, key=f"edit_lines_{pid}")
                    with ec2:
                        edit_primary = st.text_area("Primary Lines", value="\n".join(primary_lines), height=220, key=f"edit_primary_{pid}")
                with col_actions:
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    if st.button("Save", key=f"save_{pid}", use_container_width=True):
                        if not edit_name.strip():
                            st.warning("Name cannot be empty.")
                        elif not edit_lines.strip():
                            st.warning("Lines cannot be empty.")
                        else:
                            update_partner(pid, edit_name.strip(), edit_itype, edit_lines.strip(), edit_primary.strip())
                            st.success(f"**{edit_name.strip()}** updated!")
                            st.rerun()
                    st.markdown("---")
                    if st.button("Delete", key=f"del_{pid}", use_container_width=True):
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
                if primary_lines:
                    per_txt = f"# {pname}\n" + "\n".join(primary_lines)
                    st.download_button(
                        label=f"Download {pname} primary lines",
                        data=per_txt,
                        file_name=f"{pname.replace(' ', '_')}_primary_lines.txt",
                        mime="text/plain", key=f"dl_primary_{pid}"
                    )


# ============================================================
# TAB 3 — EXPORT LINES
# ============================================================
with tab_export:
    st.header("Export Partner Lines")
    st.caption("Select partners and export their ads.txt lines as a .txt file.")

    ex_partners = get_partners()
    ex_partner_map = {p[1]: p for p in ex_partners}

    if not ex_partners:
        st.warning("No partners found. Go to the Manage Partners tab to add partners first.")
    else:
        selected_export_partners = st.multiselect(
            "Select Partners",
            list(ex_partner_map.keys()),
            help="Choose one or more partners to export lines for."
        )

        primary_only = st.checkbox(
            "Only Primary Lines",
            value=False,
            help="If checked, only primary lines will be exported. Uncheck to export all lines."
        )

        if primary_only:
            st.info("Only primary lines will be included in the export, grouped by partner name.")

        st.divider()

        # Pre-generate output so download button is ready alongside generate button
        if selected_export_partners:
            sections = []
            empty_partners = []
            for pname in selected_export_partners:
                pid = ex_partner_map[pname][0]
                lines = get_partner_primary_lines(pid) if primary_only else get_partner_lines(pid)
                if lines:
                    block = f"# {pname}\n" + "\n".join(lines)
                    sections.append(block)
                else:
                    empty_partners.append(pname)
            output_txt = "\n\n".join(sections) if sections else ""
            line_type = "primary" if primary_only else "all"

            btn_col, dl_col = st.columns([1, 1])
            with btn_col:
                st.button("Generate Export", type="primary", key="btn_export", disabled=True if not output_txt else False)
            with dl_col:
                if output_txt:
                    st.download_button(
                        label=f"Download {line_type}_lines.txt",
                        data=output_txt,
                        file_name=f"{line_type}_lines.txt",
                        mime="text/plain",
                        type="primary"
                    )
                else:
                    st.button("Download", disabled=True, key="dl_disabled")

            if empty_partners:
                st.warning(f"No {'primary ' if primary_only else ''}lines found for: **{', '.join(empty_partners)}**")
        else:
            st.button("Generate Export", type="primary", key="btn_export", disabled=True)


# ============================================================
# TAB 1 — VALIDATE
# ============================================================
with tab_validate:
    partners = get_partners()
    partner_map = {p[1]: p for p in partners}
    domains, am_map = get_domains()

    with st.sidebar:
        st.subheader("Add New Domain")
        new_domain = st.text_input("Domain (e.g. example.com)")
        new_am = st.text_input("Account Manager")
        if st.button("Add Domain"):
            if new_domain:
                add_domain(new_domain.strip().lower(), new_am.strip())
                st.success(f"Added: {new_domain}")
                st.rerun()
            else:
                st.warning("Please enter a domain.")

    if not partners:
        st.warning("No partners found. Go to the **Manage Partners** tab to add your first partner.")
    else:
        col_left, col_right = st.columns([2, 1])
        with col_left:
            selected_domains = st.multiselect("Select Domains from DB", domains)
        with col_right:
            pasted_domains = st.text_area("Or paste domains (one per line / space-separated)", height=120)

        selected_partners = st.multiselect(
            "Select Partners to Validate",
            list(partner_map.keys()),
            default=list(partner_map.keys())
        )

        show_missing_lines = st.checkbox("Show missing lines in results", value=False)

        st.divider()

        def run_validation(doms_to_run, sel_partners, manual_overrides):
            results = []
            missing_det = {}
            crawler_stat = {}
            progress = st.progress(0, text="Fetching ads.txt files...")
            total = len(doms_to_run)
            for i, d in enumerate(sorted(doms_to_run)):
                progress.progress(i / total, text=f"Processing `{d}`...")
                manual_raw = manual_overrides.get(d, "").strip()
                if manual_raw:
                    live = parse_manual_lines(manual_raw)
                    crawler_stat[d] = "manual"
                else:
                    live, status = fetch_ads_txt(d)
                    crawler_stat[d] = status
                live_norm = set(norm(x) for x in live)
                for p in sel_partners:
                    pid, name, itype = partner_map[p]
                    lines = get_partner_lines(pid)
                    primary_lines = get_partner_primary_lines(pid)
                    primary_present_count = sum(1 for l in primary_lines if norm(l) in live_norm)
                    primary_total = len(primary_lines)
                    if primary_total == 0:
                        primary_status = "No primary lines set"
                    elif primary_present_count == primary_total:
                        primary_status = "Yes"
                    else:
                        primary_status = f"Partial ({primary_present_count}/{primary_total})"
                    present = [l for l in lines if norm(l) in live_norm]
                    missing = [l for l in lines if norm(l) not in live_norm]
                    total_lines = len(lines)
                    coverage_pct = round((len(present) / total_lines * 100), 1) if total_lines > 0 else 0.0
                    source_label = (
                        "Manual" if crawler_stat[d] == "manual"
                        else ("Crawler" if crawler_stat[d] == "crawler" else "Blocked")
                    )
                    results.append({
                        "Domain": d, "Account Manager": am_map.get(d, ""),
                        "Source": source_label, "Partner": name, "Integration": itype,
                        "Primary Lines Present": primary_status,
                        "Total Lines": total_lines, "Present": len(present),
                        "Missing": len(missing), "Coverage %": coverage_pct,
                    })
                    if missing and show_missing_lines:
                        missing_det.setdefault(d, {})[name] = missing
            progress.progress(1.0, text="Done!")
            return pd.DataFrame(results), missing_det, crawler_stat

        if st.button("Validate", type="primary"):
            doms = set(selected_domains)
            for d in pasted_domains.replace(",", " ").split():
                d = d.strip().lower().rstrip("/")
                if d:
                    doms.add(d)
                    if d not in domains:
                        add_domain(d, "")
            if not doms:
                st.warning("Please select or paste at least one domain.")
            elif not selected_partners:
                st.warning("Please select at least one partner.")
            else:
                df, missing_detail, crawler_status = run_validation(doms, selected_partners, {})
                st.session_state["val_results"] = df.to_dict("records")
                st.session_state["val_missing"] = missing_detail
                st.session_state["val_crawler"] = crawler_status
                st.session_state["val_doms"] = list(doms)
                st.session_state["val_partners"] = selected_partners

        if st.session_state.get("val_results"):
            df = pd.DataFrame(st.session_state["val_results"])
            missing_detail = st.session_state["val_missing"]
            crawler_status = st.session_state["val_crawler"]
            doms = set(st.session_state["val_doms"])
            selected_partners = st.session_state["val_partners"]

            blocked_doms = [d for d, s in crawler_status.items() if s == "blocked"]
            manual_used = [d for d, s in crawler_status.items() if s == "manual"]
            crawled = [d for d, s in crawler_status.items() if s == "crawler"]

            if manual_used:
                st.info(f"**Manual input used for:** `{'`, `'.join(manual_used)}`")
            if crawled:
                st.success(f"**Crawler succeeded for:** `{'`, `'.join(crawled)}`")

            if blocked_doms:
                st.warning(
                    f"**Crawler was blocked for {len(blocked_doms)} domain(s).** "
                    "Paste their ads.txt content below and click Re-validate."
                )
                rcols = st.columns(2)
                for i, d in enumerate(sorted(blocked_doms)):
                    with rcols[i % 2]:
                        st.text_area(f"{d}", height=150, key=f"revalidate_manual_{d}",
                            placeholder=f"Paste content from https://{d}/ads.txt here...")
                if st.button(f"Re-validate {len(blocked_doms)} blocked site(s)", type="primary", key="btn_revalidate"):
                    filled = {
                        d: st.session_state.get(f"revalidate_manual_{d}", "")
                        for d in blocked_doms
                        if st.session_state.get(f"revalidate_manual_{d}", "").strip()
                    }
                    if not filled:
                        st.warning("Please paste ads.txt content for at least one blocked domain.")
                    else:
                        crawled_df = df[df["Source"] != "Blocked"]
                        new_df, new_missing, new_status = run_validation(set(filled.keys()), selected_partners, filled)
                        merged_df = pd.concat([crawled_df, new_df], ignore_index=True)
                        st.session_state["val_results"] = merged_df.to_dict("records")
                        st.session_state["val_missing"] = {**missing_detail, **new_missing}
                        st.session_state["val_crawler"] = {**crawler_status, **new_status}
                        st.rerun()

            st.subheader("Summary")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Domains Checked", len(doms))
            m2.metric("Partners Checked", len(selected_partners))
            m3.metric("Avg Coverage %", f"{df['Coverage %'].mean():.1f}%")
            m4.metric("Fully Covered", df[df["Missing"] == 0].shape[0])
            primary_ok = df[df["Primary Lines Present"] == "Yes"].shape[0]
            m5.metric("Primary Lines OK", f"{primary_ok}/{len(df)}")

            st.divider()
            st.subheader("Results")

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
                if val == "Yes":
                    return "background-color: #d4edda; color: #155724"
                elif val == "No primary lines set":
                    return "background-color: #e2e3e5; color: #383d41"
                elif "Partial" in str(val):
                    return "background-color: #fff3cd; color: #856404"
                else:
                    return "background-color: #f8d7da; color: #721c24"

            styled = (
                df.style
                .map(highlight_coverage, subset=["Coverage %"])
                .map(highlight_primary, subset=["Primary Lines Present"])
            )
            st.dataframe(styled, use_container_width=True)

            if show_missing_lines and missing_detail:
                st.subheader("Missing Lines Detail")
                for domain, partners_info in missing_detail.items():
                    with st.expander(f"{domain}"):
                        for partner_name, lines in partners_info.items():
                            st.markdown(f"**{partner_name}** — {len(lines)} missing line(s):")
                            st.code("\n".join(lines), language="text")

            st.divider()
            st.subheader("Downloads")
            dl1, dl2 = st.columns(2)
            with dl1:
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
                    label="Download Excel Report", data=excel_buf,
                    file_name="ads_txt_validation.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            with dl2:
                selected_pid_names = [(partner_map[p][0], p) for p in selected_partners]
                primary_txt = build_primary_txt(selected_pid_names)
                st.download_button(
                    label="Download Primary Lines .txt (selected partners)",
                    data=primary_txt if primary_txt else "# No primary lines found",
                    file_name="primary_lines.txt", mime="text/plain"
                )