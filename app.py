import hmac
import json
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from matplotlib.ticker import MaxNLocator

st.set_page_config(page_title="Fantasy NASCAR Draft-Order Leaderboard", layout="wide")

REFRESH_SECONDS = 15
HISTORY_FILE = Path("race_history.json")
NASCAR_LIVE_FEED_URL = "https://cf.nascar.com/live/feeds/live-feed.json"
POLYMARKET_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

st.markdown(
    """
    <style>
        .app-title {font-size: 1.4rem; font-weight: 700; margin-bottom: .5rem;}
        .status-row {display: inline-flex; gap: 1.25rem; padding: .5rem .9rem;
          background: rgba(120,120,120,.08); border-radius: 8px; margin-bottom: .75rem;
          font-size: .85rem; white-space: nowrap;}
        .status-item {display: flex; flex-direction: column; align-items: center; line-height: 1.2;}
        .status-label {font-size: .68rem; opacity: .65; text-transform: uppercase; letter-spacing: .03em;}
        .status-value {font-size: 1rem; font-weight: 700;}
        .flag-dot {display: inline-block; width: .75rem; height: .75rem; border-radius: 50%; margin-right: .3rem; vertical-align: -0.05rem; border: 1px solid rgba(0,0,0,.25);}
        .flag-green {background: #16a34a;}
        .flag-yellow {background: #facc15;}
        .flag-red {background: #dc2626;}
        .flag-white {background: #ffffff;}
        .flag-checkered {background: repeating-conic-gradient(#111 0 25%, #fff 0 50%) 50% / .4rem .4rem;}
        .flag-gray {background: #9ca3af;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="app-title">🏁 Fantasy League Draft-Order Leaderboard</div>', unsafe_allow_html=True)

ALIASES = {
    "bubba wallace": "darrell wallace",
    "shane van gisbergen": "shane van gisbergen",
    "daniel suárez": "daniel suarez",
}


def clean_feed_name(raw):
    name = str(raw).strip()
    changed = True
    while changed:
        changed = False
        for pattern in (r"^\*+\s*", r"\s*#\s*$", r"\s*\(\s*[a-zA-Z]\s*\)\s*$"):
            new_name = re.sub(pattern, "", name)
            if new_name != name:
                name, changed = new_name, True
    return name.strip()


def normalize(name):
    value = clean_feed_name(name).lower().replace(".", "")
    value = ALIASES.get(value, value)
    for suffix in (" jr", " sr", " ii", " iii"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value.strip()


def get_secret(name):
    try:
        return st.secrets[name]
    except Exception:
        return ""


def flag_info(raw_flag):
    text = str(raw_flag).strip().lower()
    numeric_map = {
        "0": ("gray", "Unknown"),
        "1": ("green", "Green"),
        "2": ("yellow", "Yellow"),
        "3": ("red", "Red"),
        "4": ("white", "White"),
        "5": ("checkered", "Checkered"),
    }
    if text in numeric_map:
        return numeric_map[text]
    if "check" in text or "finish" in text:
        return "checkered", "Checkered"
    if "green" in text:
        return "green", "Green"
    if "yellow" in text or "caution" in text:
        return "yellow", "Yellow"
    if "red" in text:
        return "red", "Red"
    if "white" in text:
        return "white", "White"
    return "gray", str(raw_flag) if str(raw_flag).strip() else "Unknown"


collector_secret = get_secret("collector_password")
collector_password = st.sidebar.text_input("Desktop collector password", type="password")
is_collector = bool(collector_secret) and hmac.compare_digest(collector_password, collector_secret)
if is_collector:
    st.sidebar.success("🟢 Collector mode active")
else:
    st.sidebar.info("👀 View-only mode")

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


@st.cache_data(ttl=5)
def get_live_feed():
    response = requests.get(NASCAR_LIVE_FEED_URL, timeout=8)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=20)
def get_polymarket_odds(slug):
    response = requests.get(POLYMARKET_GAMMA_EVENTS_URL, params={"slug": slug}, timeout=8)
    response.raise_for_status()
    events = response.json()
    odds = {}
    if not events:
        return odds
    for market in events[0].get("markets", []):
        driver_name = market.get("groupItemTitle") or market.get("question", "")
        prices = market.get("outcomePrices")
        try:
            prices = json.loads(prices) if isinstance(prices, str) else prices
            odds[normalize(driver_name)] = float(prices[0])
        except Exception:
            continue
    return odds


def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        value = json.loads(HISTORY_FILE.read_text())
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(history):
    temp_file = HISTORY_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(history, indent=2))
    temp_file.replace(HISTORY_FILE)


warning_box = st.container()
table_box = st.empty()

try:
    feed = get_live_feed()
except Exception as error:
    st.error(f"Could not reach NASCAR live feed: {error}")
    feed = {}

try:
    odds = get_polymarket_odds(event_slug)
    if not odds:
        st.warning(f"No Polymarket markets found for slug '{event_slug}'.")
except Exception as error:
    st.warning(f"Could not reach Polymarket odds: {error}")
    odds = {}

lap_number_feed = feed.get("lap_number", "-")
laps_to_go_raw = feed.get("laps_to_go", "-")
stage_num = feed.get("stage_num", feed.get("stage", {}).get("stage_num", "-") if isinstance(feed.get("stage"), dict) else "-")
flag_raw = feed.get("flag_state", "-")
flag_class, flag_label = flag_info(flag_raw)

try:
    laps_to_go = int(laps_to_go_raw)
except (TypeError, ValueError):
    laps_to_go = None
try:
    feed_lap = int(lap_number_feed)
except (TypeError, ValueError):
    feed_lap = None

total_laps = feed.get("scheduled_laps") or feed.get("total_laps") or feed.get("race_laps")
try:
    total_laps = int(total_laps)
except (TypeError, ValueError):
    total_laps = None

if total_laps is not None and laps_to_go is not None:
    current_lap = total_laps - laps_to_go
elif feed_lap is not None:
    current_lap = feed_lap + 1
else:
    current_lap = None

if "derived_total_laps" not in st.session_state:
    st.session_state.derived_total_laps = None
if total_laps is None and current_lap is not None and laps_to_go is not None:
    if st.session_state.derived_total_laps is None:
        st.session_state.derived_total_laps = current_lap + laps_to_go
    total_laps = st.session_state.derived_total_laps
    current_lap = total_laps - laps_to_go

lap_display = current_lap if current_lap is not None else lap_number_feed

st.markdown(
    f"""
    <div class="status-row">
      <div class="status-item"><span class="status-label">Lap</span><span class="status-value">{lap_display}</span></div>
      <div class="status-item"><span class="status-label">Laps To Go</span><span class="status-value">{laps_to_go_raw}</span></div>
      <div class="status-item"><span class="status-label">Stage</span><span class="status-value">{stage_num}</span></div>
      <div class="status-item"><span class="status-label">Flag</span><span class="status-value"><span class="flag-dot flag-{flag_class}"></span>{flag_label}</span></div>
    </div>
    """,
    unsafe_allow_html=True,
)

vehicles = feed.get("vehicles", [])
rows = []
matched_team_keys = set()
for vehicle in vehicles:
    raw_name = vehicle.get("driver_name") or vehicle.get("driver", {}).get("full_name") or vehicle.get("full_name") or "Unknown"
    driver_name = clean_feed_name(raw_name)
    key = normalize(driver_name)
    position = vehicle.get("running_position", vehicle.get("position"))
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
        "Laps Completed": vehicle.get("laps_completed"),
        "Live Win Odds": odds.get(key),
    })

team_rows = [row for row in rows if row["_is_team_driver"]]
team_rows_sorted = sorted(team_rows, key=lambda row: (row["Position"] is None, row["Position"]))
draft_rank_by_key = {row["_key"]: index + 1 for index, row in enumerate(team_rows_sorted)}
for row in rows:
    row["Live Draft Position"] = draft_rank_by_key.get(row["_key"], "—")

df = pd.DataFrame(rows)
if not df.empty:
    df = df.sort_values("Position", na_position="last")
    df["Live Win Odds"] = df["Live Win Odds"].apply(lambda value: f"{value * 100:.1f}%" if pd.notnull(value) else "—")
    df = df.drop(columns=["_key", "_is_team_driver"])
    df = df[["Position", "Live Draft Position", "Driver (Team)", "Laps Completed", "Live Win Odds"]]
    table_box.dataframe(df, use_container_width=True, hide_index=True)
else:
    table_box.info("No live vehicle data right now.")

unmatched = [team_map[key] for key in team_map if key not in matched_team_keys]
if unmatched and vehicles:
    names = ", ".join(f"{team} → \"{driver}\"" for team, driver in unmatched)
    with warning_box:
        st.warning(f"{len(unmatched)} of {expected_teams} assigned drivers were not matched: {names}")
elif unmatched and not vehicles:
    with warning_box:
        st.info("The live field is not loaded yet, so matching cannot be verified.")

history = load_history()
is_final_lap = laps_to_go == 0
should_record = current_lap is not None and (current_lap == 1 or current_lap % 10 == 0 or is_final_lap)
recorded_laps = {int(item["lap"]) for item in history if "lap" in item}

if is_collector and should_record and current_lap not in recorded_laps:
    history.append({
        "lap": current_lap,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "positions": {
            team: draft_rank_by_key[key]
            for key, (team, _) in team_map.items()
            if key in draft_rank_by_key
        },
    })
    history.sort(key=lambda item: item["lap"])
    try:
        save_history(history)
        st.sidebar.caption(f"Last saved lap: {current_lap}")
    except OSError as error:
        st.sidebar.error(f"Could not save race history: {error}")

st.markdown("#### Live Draft Order by Lap")
if history:
    fig, ax = plt.subplots(figsize=(4.0, 4.2), dpi=160)
    colors = plt.cm.tab10(np.linspace(0, 1, max(10, expected_teams)))
    for index, (team, _) in enumerate(team_map.values()):
        points = [(int(item["lap"]), item["positions"][team]) for item in history if team in item.get("positions", {})]
        if not points:
            continue
        xs, ys = zip(*points)
        color = colors[index % len(colors)]
        ax.plot(xs, ys, marker="o", markersize=3.5, linewidth=1.6, color=color)
        ax.annotate(team, (xs[-1], ys[-1]), color=color, fontsize=7.5, fontweight="bold", xytext=(4, 0), textcoords="offset points", va="center")
    ax.set_xlabel("Lap", fontsize=9)
    ax.set_ylabel("Draft Order", fontsize=9)
    ax.set_ylim(10.6, 0.4)
    ax.set_yticks(range(1, 11))
    if total_laps:
        ax.set_xlim(1, total_laps)
        tick_step = 50 if total_laps >= 200 else max(10, total_laps // 5)
        ax.set_xticks(range(1, total_laps + 1, tick_step))
    else:
        ax.set_xlim(left=1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.3, linewidth=0.5)
    fig.tight_layout()
    image = BytesIO()
    fig.savefig(image, format="png", bbox_inches="tight")
    plt.close(fig)
    image.seek(0)
    st.image(image, width=380)
else:
    st.info("Draft-order history will appear once the collector records lap 1.")

st.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')} — auto-refreshing every {REFRESH_SECONDS}s")
time.sleep(REFRESH_SECONDS)
st.rerun()
