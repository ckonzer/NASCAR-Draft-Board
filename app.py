import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime

st.set_page_config(page_title="Fantasy NASCAR Draft-Order Leaderboard", layout="wide")

REFRESH_SECONDS = 15
NASCAR_LIVE_FEED_URL = "https://cf.nascar.com/live/feeds/live-feed.json"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com/events"

st.title("🏁 Fantasy League Draft-Order Leaderboard")
st.caption("Every driver in the field, live position, with your fantasy team name tagged on the ones that are drafted.")

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
        team_map[driver.strip().lower()] = team.strip()

expected_teams = len(team_map)
st.sidebar.caption(f"{expected_teams} team assignment(s) loaded.")

market_keyword = st.sidebar.text_input(
    "Polymarket search keyword (race/event name)", value="Cook Out 400"
)

# ---------------------------------------------------------------------------
# 2. Pull live NASCAR field
# ---------------------------------------------------------------------------
@st.cache_data(ttl=5)
def get_live_feed():
    r = requests.get(NASCAR_LIVE_FEED_URL, timeout=8)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=20)
def get_polymarket_odds(keyword):
    r = requests.get(POLYMARKET_GAMMA_URL, params={"limit": 20, "search": keyword}, timeout=8)
    r.raise_for_status()
    events = r.json()
    odds = {}
    for ev in events:
        for m in ev.get("markets", []):
            outcomes = m.get("outcomes")
            prices = m.get("outcomePrices")
            if not outcomes or not prices:
                continue
            try:
                outcomes = eval(outcomes) if isinstance(outcomes, str) else outcomes
                prices = eval(prices) if isinstance(prices, str) else prices
            except Exception:
                continue
            for name, price in zip(outcomes, prices):
                try:
                    odds[name.strip().lower()] = float(price)
                except Exception:
                    pass
    return odds

status_box = st.empty()
warning_box = st.container()
table_box = st.empty()

try:
    feed = get_live_feed()
except Exception as e:
    st.error(f"Could not reach NASCAR live feed (only returns real data during an active race weekend): {e}")
    feed = {}

try:
    odds = get_polymarket_odds(market_keyword)
except Exception as e:
    st.warning(f"Could not reach Polymarket odds: {e}")
    odds = {}

lap_number = feed.get("lap_number", "-")
laps_to_go = feed.get("laps_to_go", "-")
stage_num = feed.get("stage_num", feed.get("stage", {}).get("stage_num", "-") if isinstance(feed.get("stage"), dict) else "-")
flag_state = feed.get("flag_state", "-")

with status_box.container():
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Lap", lap_number)
    c2.metric("Laps To Go", laps_to_go)
    c3.metric("Stage", stage_num)
    c4.metric("Flag", flag_state)

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
    key = driver_name.strip().lower()
    team = team_map.get(key)
    if team:
        matched_team_keys.add(key)
        display_name = f"{driver_name} ({team})"
    else:
        display_name = driver_name

    rows.append({
        "Position": v.get("running_position", v.get("position")),
        "Driver": display_name,
        "Laps Completed": v.get("laps_completed"),
        "Win Odds": odds.get(key),
    })

df = pd.DataFrame(rows)
if not df.empty:
    df = df.sort_values("Position", na_position="last")
    if "Win Odds" in df.columns:
        df["Win Odds"] = df["Win Odds"].apply(lambda x: f"{x*100:.1f}%" if pd.notnull(x) else "—")
    table_box.dataframe(df, use_container_width=True, hide_index=True)
else:
    table_box.info("No live vehicle data right now. This feed only populates during an active NASCAR race.")

# ---------------------------------------------------------------------------
# 4. Flag any drafted driver not found in the current field
# ---------------------------------------------------------------------------
unmatched = [
    (team_map[k], k) for k in team_map if k not in matched_team_keys
]
if unmatched and vehicles:
    with warning_box:
        names = ", ".join(f"{team} → \"{driver.title()}\"" for team, driver in unmatched)
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
