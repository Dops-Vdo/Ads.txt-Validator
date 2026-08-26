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
BUSINESS_UNITS = ["Demand", "DV"]

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
    rows = sb.table("partners").select("id, name, integration_type, banner_eligible, business_units").order("name").execute().data
    return [
        (
            r["id"],
            r["name"],
            r["integration_type"] or "",
            bool(r.get("banner_eligible", False)),
            r.get("business_units") or ["Demand"],  # default to Demand if not set
        )
        for r in rows
    ]


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
def add_partner(name: str, itype: str, lines_raw: str, primary_lines_raw: str, banner_eligible: bool = False, business_units: list = None):
    if business_units is None:
        business_units = ["Demand"]
    sb = get_supabase()
    sb.table("partners").upsert(
        {"name": name, "integration_type": itype, "banner_eligible": banner_eligible, "business_units": business_units},
        on_conflict="name"
    ).execute()
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


def update_partner(pid: int, new_name: str, new_itype: str, lines_raw: str, primary_lines_raw: str, banner_eligible: bool = False, business_units: list = None):
    if business_units is None:
        business_units = ["Demand"]
    sb = get_supabase()
    sb.table("partners").update(
        {"name": new_name, "integration_type": new_itype, "banner_eligible": banner_eligible, "business_units": business_units}
    ).eq("id", pid).execute()
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
# HELPER — Business Unit widget (multi-select checkboxes)
# ==========================
def business_unit_widget(key_prefix: str, current_units: list = None):
    if current_units is None:
        current_units = ["Demand"]
    st.markdown("**Business Unit(s)**")
    cols = st.columns(len(BUSINESS_UNITS))
    selected = []
    for i, bu in enumerate(BUSINESS_UNITS):
        with cols[i]:
            checked = st.checkbox(bu, value=(bu in current_units), key=f"{key_prefix}_bu_{bu}")
            if checked:
                selected.append(bu)
    if not selected:
        st.warning("⚠️ Please select at least one Business Unit.")
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


def extract_domain_part(line: str) -> str:
    """Extract the first token (domain/seller) before the first comma from an ads.txt line."""
    parts = line.split(",")
    return parts[0].strip().lower() if parts else ""


def check_dv_line_match(line: str, live_norm_set: set, live_lines_raw: list) -> str:
    """
    For DV unit: check if a partner line is a full match, domain-only match, or missing.
    Returns: "Full Match", "Domain Only", or "Missing"
    """
    line_norm = norm(line)
    if line_norm in live_norm_set:
        return "Full Match"
    # Check domain-only: does the first part of this line appear as the first part of any live line?
    partner_domain = extract_domain_part(line)
    if partner_domain:
        for live_line in live_lines_raw:
            live_domain = extract_domain_part(live_line)
            if live_domain == partner_domain:
                return "Domain Only"
    return "Missing"


# ==========================
# HELPER — DV identifier ("ID") matching
# A partner line WITHOUT a comma is treated as a bare identifier instead of a
# full ads.txt row:
#   - purely numeric (optionally prefixed "pub-")  -> Google reseller pub-id
#   - anything else (e.g. "vdo.ai")                -> direct domain
# ==========================
def classify_identifier(raw: str):
    """Classify a bare partner identifier as ('reseller', clean_id) or ('domain', clean_domain)."""
    val = raw.strip().lower()
    if val.startswith("pub-"):
        val = val[4:]
    if val.isdigit():
        return "reseller", val
    return "domain", val


VALID_RELATIONSHIPS = {"direct", "reseller"}


def check_id_match(id_type: str, id_value: str, live_lines_raw: list):
    """
    Check whether a classified identifier appears in the live ads.txt lines.
    Returns the matched relationship ("DIRECT" or "RESELLER") on success, or None.

    A domain-type identifier (e.g. "vdo.ai") is accepted whether the live line
    marks it DIRECT or RESELLER — both count as the domain being present.
    A reseller-type identifier (google.com pub-id) is likewise accepted as
    either DIRECT or RESELLER, though in practice these are almost always RESELLER.
    In both cases, the relationship field must actually be present and be a
    recognized value (DIRECT/RESELLER) — a malformed or missing relationship
    field does not count as a confirmed match.
    """
    for live_line in live_lines_raw:
        parts = [p.strip().lower() for p in live_line.split(",")]
        if not parts or not parts[0]:
            continue
        line_domain = parts[0]
        relationship = parts[2].strip().lower() if len(parts) > 2 else ""
        if relationship not in VALID_RELATIONSHIPS:
            continue
        if id_type == "domain":
            if line_domain == id_value:
                return relationship.upper()
        else:  # reseller
            if line_domain == "google.com" and len(parts) > 1:
                pubid = parts[1].strip()
                if pubid.startswith("pub-"):
                    pubid = pubid[4:]
                if pubid == id_value:
                    return relationship.upper()
    return None


def build_dv_summary_txt(dv_summary: dict) -> str:
    """Build the plain-text 'Present lines are ...' + go-live summary for DV ID-match results."""
    lines_out = []
    go_live_domains = []
    for domain in sorted(dv_summary.keys()):
        data = dv_summary[domain]
        matched = list(dict.fromkeys(data.get("matched_ids", [])))  # dedupe, keep order
        if matched:
            if len(matched) == 1:
                joined = matched[0]
            else:
                joined = ", ".join(matched[:-1]) + " & " + matched[-1]
            lines_out.append(f"{domain}: Present lines are {joined}")
        else:
            lines_out.append(f"{domain}: No ID-based lines present")
        if data.get("eligible_partners"):
            go_live_domains.append(domain)
    lines_out.append("")
    if go_live_domains:
        lines_out.append(f"We can go live with: {', '.join(go_live_domains)}")
    else:
        lines_out.append("We can go live with: (none — no domain currently has both a direct line and a Google-reseller line present)")
    return "\n".join(lines_out)


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

    id_match_help = (
        "For **DV** partners you can enter either a full ads.txt line (comma-separated, as usual), "
        "or a bare identifier on its own line to enable ID-based matching:\n"
        "- a domain (e.g. `vdo.ai`) → checked as a **direct** line\n"
        "- a numeric publisher ID, with or without `pub-` (e.g. `7094677798399606`) → checked against **google.com RESELLER** lines"
    )

    with st.expander("Add New Partner", expanded=len(partners) == 0):
        np_name = st.text_input("Partner Name", key="np_name")
        np_itype = integration_type_widget("np")
        np_bus_units = business_unit_widget("np", ["Demand"])
        st.caption(id_match_help)
        col_lines, col_primary = st.columns(2)
        with col_lines:
            np_lines = st.text_area("All Ads.txt Lines (one per line)", height=220, key="np_lines",
                placeholder="pubmatic.com, 123456, DIRECT, abc123\nappnexus.com, 789, RESELLER\nvdo.ai\n7094677798399606\n...")
        with col_primary:
            np_primary = st.text_area("Primary Lines (subset — for approvals & publisher output)", height=220,
                key="np_primary", placeholder="pubmatic.com, 123456, DIRECT, abc123\n...")
        np_banner = st.checkbox("Banner Eligible", value=False, key="np_banner")
        if st.button("Add Partner", type="primary", key="btn_add_partner"):
            if not np_name.strip():
                st.warning("Partner name is required.")
            elif np_name.strip() in partner_map:
                st.error(f"Partner **{np_name.strip()}** already exists.")
            elif not np_lines.strip():
                st.warning("Please paste at least one ads.txt line.")
            elif not np_bus_units:
                st.warning("Please select at least one Business Unit.")
            else:
                add_partner(np_name.strip(), np_itype, np_lines.strip(), np_primary.strip(), np_banner, np_bus_units)
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

        for pid, pname, pitype, pbanner, pbus_units in filtered:
            lines = get_partner_lines(pid)
            primary_lines = get_partner_primary_lines(pid)
            banner_label = " | Banner Eligible" if pbanner else ""
            bu_label = " | " + ", ".join(pbus_units) if pbus_units else " | Demand"
            with st.expander(f"**{pname}** — {pitype or 'N/A'} — {len(lines)} line(s) | {len(primary_lines)} primary{banner_label}{bu_label}"):
                col_info, col_actions = st.columns([3, 1])
                with col_info:
                    edit_name = st.text_input("Partner Name", value=pname, key=f"edit_name_{pid}")
                    edit_itype = integration_type_widget(f"edit_{pid}", current_value=pitype or "")
                    edit_bus_units = business_unit_widget(f"edit_{pid}", current_units=pbus_units or ["Demand"])
                    edit_banner = st.checkbox("Banner Eligible", value=pbanner, key=f"edit_banner_{pid}")
                    if "DV" in (pbus_units or []):
                        st.caption(id_match_help)
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
                        elif not edit_bus_units:
                            st.warning("Select at least one Business Unit.")
                        else:
                            update_partner(pid, edit_name.strip(), edit_itype, edit_lines.strip(), edit_primary.strip(), edit_banner, edit_bus_units)
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

        ex_col1, ex_col2, ex_col3 = st.columns(3)
        with ex_col1:
            primary_only = st.checkbox(
                "Only Primary Lines",
                value=False,
                key="exp_primary_only",
                help="Export only primary lines for selected partners."
            )
        with ex_col2:
            banner_only = st.checkbox(
                "Only Banner Eligible Partners",
                value=False,
                key="exp_banner_only",
                help="If checked, only partners marked as Banner Eligible will be included."
            )
        with ex_col3:
            export_bu_filter = st.selectbox(
                "Filter by Business Unit",
                ["All"] + BUSINESS_UNITS,
                key="exp_bu_filter",
                help="Filter exported partners by Business Unit."
            )

        st.divider()

        if selected_export_partners:
            sections = []
            empty_partners = []
            for pname in selected_export_partners:
                pid, pname_db, pitype, pbanner, pbus_units = ex_partner_map[pname]
                if banner_only and not pbanner:
                    empty_partners.append(f"{pname} (not banner eligible)")
                    continue
                if export_bu_filter != "All" and export_bu_filter not in (pbus_units or ["Demand"]):
                    empty_partners.append(f"{pname} (not in {export_bu_filter} BU)")
                    continue
                lines = get_partner_primary_lines(pid) if primary_only else get_partner_lines(pid)
                if lines:
                    block = f"# {pname}\n" + "\n".join(lines)
                    sections.append(block)
                else:
                    empty_partners.append(pname)
            output_txt = "\n\n".join(sections) if sections else ""
            line_type = "primary" if primary_only else "all"
            if banner_only:
                line_type = f"banner_{line_type}"

            btn_col, dl_col = st.columns([1, 1])
            with btn_col:
                st.button("Generate Export", type="primary", key="btn_export", disabled=not bool(output_txt))
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
                st.warning(f"Skipped: **{', '.join(empty_partners)}**")
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
        # ── Business Unit toggle ──
        st.markdown("### Business Unit")
        bu_col1, bu_col2 = st.columns([3, 1])
        with bu_col1:
            active_bu = st.radio(
                "Validate for Business Unit:",
                BUSINESS_UNITS,
                index=0,
                horizontal=True,
                key="active_bu",
                help="Select which business unit to validate. DV uses partial (domain-only) matching plus ID-based matching for bare identifiers."
            )
        with bu_col2:
            if active_bu == "DV":
                st.info("🔍 DV Mode: partial + ID matching enabled")

        st.divider()

        col_left, col_right = st.columns([2, 1])
        with col_left:
            selected_domains = st.multiselect("Select Domains from DB", domains)
        with col_right:
            pasted_domains = st.text_area("Or paste domains (one per line / space-separated)", height=120)

        # Filter partners by active BU
        bu_filtered_partners = [
            p for p in partners
            if active_bu in (p[4] or ["Demand"])
        ]
        bu_partner_names = [p[1] for p in bu_filtered_partners]

        if not bu_partner_names:
            st.warning(f"No partners assigned to **{active_bu}** business unit. Go to Manage Partners to assign partners.")
        else:
            selected_partners = st.multiselect(
                f"Select Partners to Validate ({active_bu} BU)",
                bu_partner_names,
                default=bu_partner_names
            )

            show_missing_lines = st.checkbox("Show missing lines in results", value=False)

            st.divider()

            def run_validation(doms_to_run, sel_partners, manual_overrides, bu_mode="Demand"):
                results = []
                missing_det = {}
                crawler_stat = {}
                dv_summary = {}  # domain -> {"matched_ids": [...], "eligible_partners": [...]}
                progress = st.progress(0, text="Fetching ads.txt files...")
                total = len(doms_to_run)
                is_dv = (bu_mode == "DV")

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

                    if is_dv:
                        dv_summary.setdefault(d, {"matched_ids": [], "eligible_partners": []})

                    for p in sel_partners:
                        pid, name, itype, pbanner, pbus_units = partner_map[p]
                        lines = get_partner_lines(pid)
                        primary_lines = get_partner_primary_lines(pid)

                        # Primary lines check (always exact)
                        primary_present_count = sum(1 for l in primary_lines if norm(l) in live_norm)
                        primary_total = len(primary_lines)
                        if primary_total == 0:
                            primary_status = "No primary lines set"
                        elif primary_present_count == primary_total:
                            primary_status = "Yes"
                        else:
                            primary_status = f"Partial ({primary_present_count}/{primary_total})"

                        if is_dv:
                            # DV mode: classify each line.
                            # Lines WITH a comma -> Full Match / Domain Only / Missing (existing logic).
                            # Lines WITHOUT a comma -> bare identifier -> ID Match (direct domain or
                            # google reseller pub-id) via check_id_match.
                            full_matches = []
                            domain_only_matches = []
                            id_matches = []
                            missing_lines = []
                            partner_has_direct = False
                            partner_has_reseller = False

                            for l in lines:
                                if "," in l:
                                    result = check_dv_line_match(l, live_norm, live)
                                    if result == "Full Match":
                                        full_matches.append(l)
                                    elif result == "Domain Only":
                                        domain_only_matches.append(l)
                                    else:
                                        missing_lines.append(l)
                                else:
                                    id_type, id_value = classify_identifier(l)
                                    matched_relationship = check_id_match(id_type, id_value, live)
                                    if matched_relationship:
                                        id_matches.append(l.strip())
                                        if id_type == "domain":
                                            partner_has_direct = True
                                        else:
                                            partner_has_reseller = True
                                    else:
                                        missing_lines.append(l)

                            total_lines = len(lines)
                            confirmed = len(full_matches) + len(id_matches)
                            full_pct = round((confirmed / total_lines * 100), 1) if total_lines > 0 else 0.0
                            any_pct = round(((confirmed + len(domain_only_matches)) / total_lines * 100), 1) if total_lines > 0 else 0.0

                            source_label = (
                                "Manual" if crawler_stat[d] == "manual"
                                else ("Crawler" if crawler_stat[d] == "crawler" else "Blocked")
                            )
                            results.append({
                                "Domain": d,
                                "Source": source_label,
                                "Partner": name,
                                "Integration": itype,
                                "Banner Eligible": "Yes" if pbanner else "No",
                                "Business Unit": ", ".join(pbus_units or ["Demand"]),
                                "Primary Lines Present": primary_status,
                                "Total Lines": total_lines,
                                "Full Matches": len(full_matches),
                                "ID Matches": len(id_matches),
                                "Domain-Only Matches": len(domain_only_matches),
                                "Missing": len(missing_lines),
                                "Full Match %": full_pct,
                                "Any Match %": any_pct,
                                "Go-Live Ready": "Yes" if (partner_has_direct and partner_has_reseller) else "No",
                            })
                            if missing_lines and show_missing_lines:
                                missing_det.setdefault(d, {})[name] = missing_lines
                            if domain_only_matches and show_missing_lines:
                                missing_det.setdefault(d, {}).setdefault(f"{name} [Domain-Only]", domain_only_matches)

                            dv_summary[d]["matched_ids"].extend(id_matches)
                            if partner_has_direct and partner_has_reseller:
                                dv_summary[d]["eligible_partners"].append(name)
                        else:
                            # Demand mode: exact matching (original logic)
                            present = [l for l in lines if norm(l) in live_norm]
                            missing = [l for l in lines if norm(l) not in live_norm]
                            total_lines = len(lines)
                            coverage_pct = round((len(present) / total_lines * 100), 1) if total_lines > 0 else 0.0
                            source_label = (
                                "Manual" if crawler_stat[d] == "manual"
                                else ("Crawler" if crawler_stat[d] == "crawler" else "Blocked")
                            )
                            results.append({
                                "Domain": d,
                                "Source": source_label,
                                "Partner": name,
                                "Integration": itype,
                                "Banner Eligible": "Yes" if pbanner else "No",
                                "Business Unit": ", ".join(pbus_units or ["Demand"]),
                                "Primary Lines Present": primary_status,
                                "Total Lines": total_lines,
                                "Present": len(present),
                                "Missing": len(missing),
                                "Coverage %": coverage_pct,
                            })
                            if missing and show_missing_lines:
                                missing_det.setdefault(d, {})[name] = missing

                progress.progress(1.0, text="Done!")
                return pd.DataFrame(results), missing_det, crawler_stat, dv_summary

            # Detect if selections changed — clear stale results
            current_hash = str(sorted(selected_domains)) + pasted_domains + str(sorted(selected_partners)) + active_bu
            if st.session_state.get("val_selection_hash") != current_hash:
                st.session_state["val_selection_hash"] = current_hash
                st.session_state["val_results"] = None

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
                    df, missing_detail, crawler_status, dv_summary = run_validation(doms, selected_partners, {}, bu_mode=active_bu)
                    st.session_state["val_results"] = df.to_dict("records")
                    st.session_state["val_missing"] = missing_detail
                    st.session_state["val_crawler"] = crawler_status
                    st.session_state["val_dv_summary"] = dv_summary
                    st.session_state["val_doms"] = list(doms)
                    st.session_state["val_partners"] = selected_partners
                    st.session_state["val_bu_mode"] = active_bu
                    st.session_state["val_selection_hash"] = current_hash

            if st.session_state.get("val_results"):
                df = pd.DataFrame(st.session_state["val_results"])
                missing_detail = st.session_state["val_missing"]
                crawler_status = st.session_state["val_crawler"]
                dv_summary = st.session_state.get("val_dv_summary", {})
                doms = set(st.session_state["val_doms"])
                selected_partners = st.session_state["val_partners"]
                val_bu_mode = st.session_state.get("val_bu_mode", "Demand")
                is_dv_results = (val_bu_mode == "DV")

                blocked_doms = [d for d, s in crawler_status.items() if s == "blocked"]
                manual_used = [d for d, s in crawler_status.items() if s == "manual"]
                crawled = [d for d, s in crawler_status.items() if s == "crawler"]

                if manual_used:
                    st.info(f"**Manual input used for:** `{'`, `'.join(manual_used)}`")
                if crawled:
                    st.success(f"**Crawler succeeded for:** `{'`, `'.join(crawled)}`")

                st.subheader(f"Summary — {val_bu_mode} Business Unit")

                if is_dv_results:
                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("Domains Checked", len(doms))
                    m2.metric("Partners Checked", len(selected_partners))
                    m3.metric("Avg Full Match %", f"{df['Full Match %'].mean():.1f}%")
                    m4.metric("Avg Any Match %", f"{df['Any Match %'].mean():.1f}%")
                    m5.metric("Fully Matched", df[df["Missing"] == 0].shape[0])
                    primary_ok = df[df["Primary Lines Present"] == "Yes"].shape[0]
                    m6.metric("Primary Lines OK", f"{primary_ok}/{len(df)}")
                else:
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

                def highlight_banner(val):
                    if val == "Yes":
                        return "background-color: #cce5ff; color: #004085"
                    return "background-color: #e2e3e5; color: #383d41"

                def highlight_dv_match(val):
                    """Color cells for DV domain-only match count."""
                    if isinstance(val, (int, float)):
                        if val == 0:
                            return "background-color: #d4edda; color: #155724"
                        elif val > 0:
                            return "background-color: #fff3cd; color: #856404"
                    return ""

                def highlight_go_live(val):
                    if val == "Yes":
                        return "background-color: #d4edda; color: #155724; font-weight: 600"
                    return "background-color: #f8d7da; color: #721c24"

                if is_dv_results:
                    styled = (
                        df.style
                        .map(highlight_coverage, subset=["Full Match %", "Any Match %"])
                        .map(highlight_primary, subset=["Primary Lines Present"])
                        .map(highlight_banner, subset=["Banner Eligible"])
                        .map(highlight_dv_match, subset=["Domain-Only Matches"])
                        .map(highlight_go_live, subset=["Go-Live Ready"])
                    )
                    st.caption(
                        "🟢 Full Match = exact line present | 🟢 ID Match = bare identifier (direct domain / google reseller pub-id) confirmed present | "
                        "🟡 Domain-Only Match = seller domain present but line differs | 🔴 Missing = not found at all | "
                        "**Go-Live Ready** = this partner has both a matched direct line AND a matched google-reseller ID for this domain"
                    )
                else:
                    styled = (
                        df.style
                        .map(highlight_coverage, subset=["Coverage %"])
                        .map(highlight_primary, subset=["Primary Lines Present"])
                        .map(highlight_banner, subset=["Banner Eligible"])
                    )

                st.dataframe(styled, use_container_width=True)

                # ── DV ID-Match / Go-Live summary ──
                if is_dv_results and dv_summary:
                    st.divider()
                    st.subheader("DV ID-Match & Go-Live Summary")
                    for domain in sorted(dv_summary.keys()):
                        data = dv_summary[domain]
                        matched = list(dict.fromkeys(data.get("matched_ids", [])))
                        eligible = data.get("eligible_partners", [])
                        badge = "🟢 Go-Live Ready" if eligible else "⚪ Not yet"
                        with st.expander(f"{domain} — {badge}"):
                            if matched:
                                st.write("**Present lines (ID match):** " + ", ".join(matched))
                            else:
                                st.write("No ID-based identifiers matched for this domain.")
                            if eligible:
                                st.success(f"Has both direct + google-reseller lines for: {', '.join(eligible)}")

                    dv_summary_txt = build_dv_summary_txt(dv_summary)
                    st.download_button(
                        label="Download DV ID-Match Summary (.txt)",
                        data=dv_summary_txt,
                        file_name="dv_id_match_summary.txt",
                        mime="text/plain",
                        key="dl_dv_summary"
                    )

                if show_missing_lines and missing_detail:
                    st.subheader("Missing / Partial Lines Detail")
                    for domain, partners_info in missing_detail.items():
                        with st.expander(f"{domain}"):
                            for partner_name, lines in partners_info.items():
                                if "[Domain-Only]" in partner_name:
                                    st.markdown(f"**{partner_name}** — {len(lines)} line(s) with domain present but full line differs:")
                                    st.code("\n".join(lines), language="text")
                                else:
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
                                    raw_name = partner_name.replace(" [Domain-Only]", "")
                                    banner_val = "Yes" if partner_map.get(raw_name, (None, None, None, False, None))[3] else "No"
                                    match_type = "Domain Only" if "[Domain-Only]" in partner_name else "Missing"
                                    for line in lines:
                                        missing_rows.append({
                                            "Domain": domain,
                                            "Partner": raw_name,
                                            "Banner Eligible": banner_val,
                                            "Match Type": match_type,
                                            "Line": line
                                        })
                            if missing_rows:
                                pd.DataFrame(missing_rows).to_excel(writer, index=False, sheet_name="Missing Lines")
                        if is_dv_results and dv_summary:
                            dv_rows = []
                            for domain, data in dv_summary.items():
                                dv_rows.append({
                                    "Domain": domain,
                                    "Matched Identifiers": ", ".join(dict.fromkeys(data.get("matched_ids", []))),
                                    "Go-Live Ready": "Yes" if data.get("eligible_partners") else "No",
                                    "Eligible Partners": ", ".join(data.get("eligible_partners", [])),
                                })
                            if dv_rows:
                                pd.DataFrame(dv_rows).to_excel(writer, index=False, sheet_name="DV ID-Match Summary")
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

                # ── Blocked sites re-validate ──
                if blocked_doms:
                    st.divider()
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
                            new_df, new_missing, new_status, new_dv_summary = run_validation(set(filled.keys()), selected_partners, filled, bu_mode=val_bu_mode)
                            merged_df = pd.concat([crawled_df, new_df], ignore_index=True)
                            merged_dv_summary = dict(dv_summary)
                            for dom, data in new_dv_summary.items():
                                merged_dv_summary[dom] = data
                            st.session_state["val_results"] = merged_df.to_dict("records")
                            st.session_state["val_missing"] = {**missing_detail, **new_missing}
                            st.session_state["val_crawler"] = {**crawler_status, **new_status}
                            st.session_state["val_dv_summary"] = merged_dv_summary
                            st.rerun()