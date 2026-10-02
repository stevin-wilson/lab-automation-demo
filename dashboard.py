"""Read-only status page: device state and the event log. Simulator and synthetic data only."""

import os

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_URL = os.environ.get("LABDEMO_API_URL", "http://127.0.0.1:8000")
STATE_COLORS = {
    "IDLE": "green",
    "BUSY": "orange",
    "OFFLINE": "gray",
    "ERROR": "red",
    "UNKNOWN_OUTCOME": "red",
    "NEEDS_HUMAN": "red",
}
ROW_COLORS = {
    "rejected": "#f8d7da",
    "refused": "#f8d7da",
    "failed": "#f8d7da",
    "duplicate": "#fff3cd",
}

st.set_page_config(page_title="labdemo status", layout="wide")
st.title("Simulated lab: device status")
st.caption("Simulator and synthetic data only. Not a real instrument.")
st.button("Refresh")

try:
    device = httpx.get(f"{API_URL}/device", timeout=5).json()
    events = httpx.get(f"{API_URL}/log", params={"limit": 200}, timeout=5).json()["events"]
except (httpx.HTTPError, ValueError, KeyError) as exc:
    st.error(f"Cannot reach the API at {API_URL}: {exc}")
    st.stop()

state = device["state"]
st.markdown(f"### Device: :{STATE_COLORS.get(state, 'gray')}[{state}]")
st.write(f"In this state since {device['since']}. Fault armed: {device['armed_fault']}.")

st.subheader("Event log (newest first)")
frame = pd.DataFrame(events, columns=["at", "kind", "command_id", "detail"])


def highlight(row: pd.Series) -> list[str]:
    colour = ROW_COLORS.get(row["kind"], "")
    return [f"background-color: {colour}" if colour else ""] * len(row)


st.dataframe(frame.style.apply(highlight, axis=1), hide_index=True, width="stretch")
