"""Streamlit UI: filter and search the local CotC SQLite mirror.

Run with: streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from config import (
    DB_PATH,
    SPREADSHEET_URL,
    USER_CONFIG_DIR,
    USER_CONFIG_PATH,
)
from db import repo
from sync.runner import run_sync


# --- API key persistence ----------------------------------------------------

def load_api_key() -> str:
    if USER_CONFIG_PATH.exists():
        try:
            for line in USER_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
                if line.startswith("api_key"):
                    _, _, val = line.partition("=")
                    return val.strip().strip('"').strip("'")
        except OSError:
            pass
    return os.environ.get("GOOGLE_API_KEY", "")


def save_api_key(key: str) -> None:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.write_text(f'api_key = "{key}"\n', encoding="utf-8")


# --- helpers ----------------------------------------------------------------

def _conn():
    """Open a fresh SQLite connection per call.

    Streamlit reruns may land on different worker threads, and sqlite3
    connections are bound to the thread that created them. Caching the
    connection in session_state therefore raises ProgrammingError. Opening a
    new connection each call is cheap (file-based DB) and side-steps the
    issue. Schema bootstrap only runs once per session via a flag.
    """
    c = repo.connect()
    if not st.session_state.get("_db_bootstrapped"):
        repo.bootstrap(c)
        st.session_state["_db_bootstrapped"] = True
    return c


def _row_label(row) -> str:
    rarity = row["rarity"] or "?"
    return f"{row['display_name']} — {row['base_role'] or '?'}/{row['base_weapon'] or '?'} · {rarity}"


# --- UI ---------------------------------------------------------------------

st.set_page_config(page_title="CotC Character Search", layout="wide")
st.title("Octopath: CotC — Character Search")
st.caption(f"Local mirror of [the community sheet]({SPREADSHEET_URL}).")

if not Path(DB_PATH).exists() or repo.counts(_conn())["character_forms"] == 0:
    st.info("Database is empty. Set your API key below and click **Refresh** to populate.")

# --- top control row: API key + Refresh + last-sync timestamp ---------------
with st.expander("⚙️  Settings & sync", expanded=not Path(DB_PATH).exists()):
    saved_key = load_api_key()
    api_key = st.text_input(
        "Google Sheets API key (read-only)",
        value=saved_key,
        type="password",
        help="A free read-only key from console.cloud.google.com (Sheets API enabled). "
             "Stored at ~/.cotc-search/config.toml so it isn't committed to the project.",
    )
    cols = st.columns([1, 1, 4])
    with cols[0]:
        do_refresh = st.button("🔄 Refresh from Sheets", type="primary",
                               disabled=not api_key, width="stretch")
    with cols[1]:
        save_btn = st.button("💾 Save key", disabled=not api_key, width="stretch")
    with cols[2]:
        last = repo.latest_sync_run(_conn())
        if last:
            st.write(f"Last sync: **{last['finished_at'] or last['started_at']}** "
                     f"· status: `{last['status']}`")
        else:
            st.write("No sync runs yet.")

    if save_btn and api_key:
        save_api_key(api_key)
        st.success("Saved.")

    if do_refresh and api_key:
        save_api_key(api_key)
        progress_box = st.empty()
        log_lines: list[str] = []

        def _push(msg: str) -> None:
            log_lines.append(msg)
            progress_box.code("\n".join(log_lines[-20:]))

        try:
            with st.spinner("Syncing..."):
                summary = run_sync(api_key, progress=_push)
            st.success(f"Sync complete: {summary}")
        except Exception as exc:
            st.error(f"Sync failed: {exc}")

# --- filter bar -------------------------------------------------------------

conn = _conn()
roles = repo.role_choices(conn)
weapons = repo.weapon_choices(conn)
rarities = repo.rarity_choices(conn)
weaknesses = repo.affinity_choices(conn, "weakness")

st.subheader("Filters")
fc = st.columns([2, 2, 2, 2, 3])
sel_roles = fc[0].multiselect("Role", roles, default=[])
sel_weapons = fc[1].multiselect("Weapon", weapons, default=[])
sel_rarity = fc[2].multiselect("Rarity", rarities, default=[])
sel_weak = fc[3].multiselect("Weakness", weaknesses, default=[])
free_text = fc[4].text_input(
    "Free-text search (skills, equipment, names)", value="",
    placeholder="e.g. fire damage up",
)

results = repo.search_forms(
    conn,
    roles=sel_roles or None,
    weapons=sel_weapons or None,
    rarities=sel_rarity or None,
    weaknesses=sel_weak or None,
    text=free_text or None,
    limit=500,
)

# --- two-pane layout --------------------------------------------------------

left, right = st.columns([2, 3])

with left:
    st.write(f"**{len(results)}** forms match your filters")
    if not results:
        st.write("_No matches. Loosen the filters or run a refresh._")
    else:
        labels = {r["form_id"]: _row_label(r) for r in results}
        # If selection still valid keep it, else default to first.
        prev_sel = st.session_state.get("selected_form_id")
        default_sel = prev_sel if prev_sel in labels else next(iter(labels))
        selected = st.radio(
            "Select a character form:",
            options=list(labels.keys()),
            format_func=lambda k: labels[k],
            index=list(labels.keys()).index(default_sel),
            label_visibility="collapsed",
        )
        st.session_state["selected_form_id"] = selected

with right:
    sel_id = st.session_state.get("selected_form_id")
    if not sel_id:
        st.write("_Select a character on the left to see details._")
    else:
        form = repo.get_form(conn, sel_id)
        if not form:
            st.write("_Form no longer exists; refresh._")
        else:
            color = form["name_color_hex"] or "#000"
            st.markdown(
                f"### <span style='color:{color}'>{form['display_name']}</span> "
                f"<small>· {form['base_role']}/{form['base_weapon']} "
                f"· {form['rarity'] or '?'} · {form['server']}</small>",
                unsafe_allow_html=True,
            )
            if form["hyperlink_url"]:
                st.markdown(f"[Open in Google Sheets ↗]({form['hyperlink_url']})")

            affs = repo.get_affinities(conn, sel_id)
            if affs:
                st.write("**Affinities**")
                groups: dict[str, list[str]] = {}
                for a in affs:
                    groups.setdefault(a["kind"], []).append(a["icon_label"] or "?")
                for kind in ("weapon", "element", "weakness", "trait"):
                    if kind in groups:
                        st.write(f"- **{kind}**: " + ", ".join(groups[kind]))

            skills = repo.get_skills(conn, sel_id)
            if skills:
                st.write("**Skills**")
                rows = []
                for s in skills:
                    desc = s["description"] or ""
                    if s["kind"] == "latent" and (s["initial_use"] or s["cooldown"]):
                        prefix_bits = []
                        if s["initial_use"]:
                            prefix_bits.append(f"init {s['initial_use']}t")
                        if s["cooldown"]:
                            prefix_bits.append(f"cd {s['cooldown']}t")
                        desc = f"[{' / '.join(prefix_bits)}] {desc}"
                    rows.append({
                        "#": s["slot_order"], "SP": s["sp_cost"], "Kind": s["kind"] or "",
                        "Board": (f"{s['learn_board']}*" if s["learn_board"] else ""),
                        "Tier":  (f"Lv{s['tier_level']}" if s["tier_level"] else ""),
                        "Description": desc,
                        "Hits": s["hits"], "Min": s["power_min"], "Max": s["power_max"],
                    })
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.write("_No skills parsed for this form._")

            equipment = repo.get_equipment(conn, sel_id)
            if equipment:
                st.write("**A4 Accessories**")
                for e in equipment:
                    badge = " *(exclusive)*" if e["is_exclusive"] else ""
                    line = f"- **{e['name']}**{badge}"
                    if e["description"]:
                        line += f" — {e['description']}"
                    st.write(line)

            profile = repo.get_profile(conn, sel_id)
            if profile and (profile["splash_art_url"] or profile["self_buffs_text"]):
                st.write("**Profile**")
                if profile["splash_art_url"]:
                    st.image(profile["splash_art_url"], width=320)
                if profile["self_buffs_text"]:
                    st.write(profile["self_buffs_text"])
