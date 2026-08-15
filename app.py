import streamlit as st
import requests
import pandas as pd
import json
import time
from datetime import datetime

st.set_page_config(page_title="Fantasy NASCAR Draft-Order Leaderboard", layout="wide")

REFRESH_SECONDS = 15
NASCAR_LIVE_FEED_URL = "https://cf.nascar.com/live/feeds/live-feed.json"
POLYMARKET_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

# ---------------------------------------------------------------------------
# Compact styling: smaller title, and a status row built from raw HTML
# (instead of st.columns/st.metric) so it only takes up as much width as
# its content needs, rather than stretching across the full page.
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        .app-title {
            font-size: 1.4rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }
        .status-row {
            display: inline-flex;
            gap: 1.25rem;
            padding: 0.5rem 0.9rem;
            background: rgba(120,120,120,0.08);
            border-radius: 8px;
            margin-bottom: 0.75rem;
            font-size: 0.85rem;
            white-space: nowrap;
        }
        .status-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            line-height: 1.2;
        }
        .status-label {
            font-size: 0.68rem;
            opacity: 0.65;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
        .status-value {
            font-size: 1rem;
            font-weight: 700;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="app-title">🏁 Fantasy League Draft-Order Leaderboard</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Name aliasing — Polymarket / NASCAR live feed / your mapping don't always
# use the same string for the same driver (legal name vs. nickname, periods
# in initials, Jr./Sr. suffixes, accents, etc.)
# ---------------------------------------------------------------------------
ALIASES = {
    "bubba wallace": "darrell wallace",
    "shane van gisbergen": "shane van gisbergen",
    "daniel suárez": "daniel suarez",
}

def normalize(name: str) -> str:
    n = name.strip().lower().replace(".", "")
    n = ALIASES.get(n, n)
    for suf in (" jr", " sr", " ii", " iii"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()

# ---------------------------------------------------------------------------
# 1. Sidebar: team -> driver mapping — your 10 teams
# ---------------------------------------------------------------------------
st.sidebar.header("Team ↔ Driver Assignments")
st.sidebar.write("Format: one pair per line -> `Team Name | Driver Full Name`")
default_mapping = """DaG | Ryan Blaney
KM | Denny Hamlin
MK | Christopher Bell
TC | Joey Logano
DM | Ty Gibbs
CA | Chase Briscoe
MR | Bubba Wallace
DiG | Austin Dillon
CK | Kyle Larson
CB | William Byron"""
mapping_text = st.sidebar.text_area("Assignments", value=default_mapping, height=280)

team_map = {}
for line in mapping_text.splitlines():
    if "|" in line:
        team, driver = line.split("|", 1)
        team_map[normalize(driver)] = (team.strip(), driver.strip())

expected_teams = len(team_map)
st.sidebar.caption(f"{expected_teams} team assignment(s) loaded.")

st.sidebar.header("Polymarket Event")
polymarket_input = st.sidebar.text_input(
    "Event slug or full URL",
    value="https://polymarket.com/event/nascar-cook-out-400-winner-2026-08-15",
)
event_slug = polymarket_input.strip().rstrip("/").split("/")[-1]

# ---------------------------------------------------------------------------
# 2. Pull live NASCAR field
# ---------------------------------------------------------------------------
@st.cache_data(ttl=5)
def get_live_feed():
    r = requests.get(NASCAR_LIVE_FEED_URL, timeout=8)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=20)
def get_polymarket_odds(slug: str):
    """Handles events built as a bundle of per-driver Yes/No markets
    (groupItemTitle = driver name, outcomePrices[0] = Yes/win probability)."""
    r = requests.get(POLYMARKET_GAMMA_EVENTS_URL, params={"slug": slug}, timeout=8)
    r.raise_for_status()
    events = r.json()
    odds = {}
    if not events:
        return odds
    event = events[0]
    for m in event.get("markets", []):
        driver_name = m.get("groupItemTitle") or m.get("question", "")
        prices = m.get("outcomePrices")
        try:
            prices = json.loads(prices) if isinstance(prices, str) else prices
            yes_price = float(prices[0])
        except Exception:
            continue
        odds[normalize(driver_name)] = yes_price
    return odds

warning_box = st.container()
table_box = st.empty()

try:
    feed = get_live_feed()
except Exception as e:
    st.error(f"Could not reach NASCAR live feed (only returns real data during an active race weekend): {e}")
    feed = {}

try:
    odds = get_polymarket_odds(event_slug)
    if not odds:
        st.warning(f"Polymarket returned no markets for slug '{event_slug}'. Double-check the URL/slug.")
except Exception as e:
    st.warning(f"Could not reach Polymarket odds: {e}")
    odds = {}

lap_number = feed.get("lap_number", "-")
laps_to_go = feed.get("laps_to_go", "-")
stage_num = feed.get("stage_num", feed.get("stage", {}).get("stage_num", "-") if isinstance(feed.get("stage"), dict) else "-")
flag_state = feed.get("flag_state", "-")

status_html = f"""
<div class="status-row">
    <div class="status-item"><span class="status-label">Lap</span><span class="status-value">{lap_number}</span></div>
    <div class="status-item"><span class="status-label">Laps To Go</span><span class="status-value">{laps_to_go}</span></div>
    <div class="status-item"><span class="status-label">Stage</span><span class="status-value">{stage_num}</span></div>
    <div class="status-item"><span class="status-label">Flag</span><span class="status-value">{flag_state}</span></div>
</div>
"""
st.markdown(status_html, unsafe_allow_html=True)

vehicles = feed.get("vehicles", [])

# ---------------------------------------------------------------------------
# 3. Build the full-field table, tagging drafted drivers with their team
# ---------------------------------------------------------------------------
rows = []
matched_team_keys = set()

for v in vehicles:
    driver_name = (
        v.get("driver_name")
        or v.get("driver", {}).get("full_name")
        or v.get("full_name")
        or "Unknown"
    )
    key = normalize(driver_name)
    position = v.get("running_position", v.get("position"))
    team_entry = team_map.get(key)
    if team_entry:
        matched_team_keys.add(key)
        display_name = f"{driver_name} ({team_entry[0]})"
    else:
        display_name = driver_name

    rows.append({
        "_key": key,
        "_is_team_driver": team_entry is not None,
        "Position": position,
        "Driver (Team)": display_name,
        "Laps Completed": v.get("laps_completed"),
        "Win Odds": odds.get(key),
    })

# ---------------------------------------------------------------------------
# 4. Compute live draft order (1-10) among just the 10 drafted drivers,
#    ranked by their current on-track position relative to each other
# ---------------------------------------------------------------------------
team_rows = [r for r in rows if r["_is_team_driver"]]
team_rows_sorted = sorted(
    team_rows,
    key=lambda r: (r["Position"] is None, r["Position"])
)
draft_rank_by_key = {r["_key"]: i + 1 for i, r in enumerate(team_rows_sorted)}

for r in rows:
    r["Live Draft Position"] = draft_rank_by_key.get(r["_key"], "—")

df = pd.DataFrame(rows)
if not df.empty:
    df = df.sort_values("Position", na_position="last")
    if "Win Odds" in df.columns:
        df["Win Odds"] = df["Win Odds"].apply(lambda x: f"{x*100:.1f}%" if pd.notnull(x) else "—")
    df = df.drop(columns=["_key", "_is_team_driver"])
    df = df[["Position", "Live Draft Position", "Driver (Team)", "Laps Completed", "Win Odds"]]
    table_box.dataframe(df, use_container_width=True, hide_index=True)
else:
    table_box.info("No live vehicle data right now. This feed only populates during an active NASCAR race.")

# ---------------------------------------------------------------------------
# 5. Flag any drafted driver not found in the current field
# ---------------------------------------------------------------------------
unmatched = [
    team_map[k] for k in team_map if k not in matched_team_keys
]
if unmatched and vehicles:
    with warning_box:
        names = ", ".join(f"{team} → \"{driver}\"" for team, driver in unmatched)
        st.warning(
            f"{len(unmatched)} of your {expected_teams} assigned drivers weren't matched in the live field "
            f"(check spelling/nicknames, e.g. 'Jr.', suffixes, or a driver who didn't qualify): {names}"
        )
elif unmatched and not vehicles:
    with warning_box:
        st.info("Live field isn't loaded yet, so team-to-driver matching can't be verified until the race feed goes active.")

st.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')} — auto-refreshing every {REFRESH_SECONDS}s")

time.sleep(REFRESH_SECONDS)
st.rerun()
