"""
dashboard.py — Live Streamlit dashboard. Polls the API every N seconds.
Shows real-time KPIs, conversion funnel, zone heatmap, and active anomalies.
Run: streamlit run dashboard.py
"""
import os, time
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

API  = os.getenv("API_URL",  "http://localhost:8000")
STORE = os.getenv("STORE_ID", "STORE_BLR_002")

st.set_page_config(page_title="Purplle Store Intelligence",
                   page_icon="💄", layout="wide")

with st.sidebar:
    st.title("💄 Store Intelligence")
    st.caption("Purplle Tech Challenge 2026")
    store_id = st.selectbox("Store", ["STORE_BLR_002", "STORE_BLR_001"])
    refresh  = st.slider("Refresh (s)", 3, 30, 5)
    try:
        h = requests.get(f"{API}/health", timeout=2).json()
        st.success(f"API {h.get('status','?')}")
    except Exception:
        st.error("API unreachable")

placeholder = st.empty()


def get(path):
    try:    return requests.get(f"{API}{path}", timeout=3).json()
    except: return None


while True:
    m = get(f"/stores/{store_id}/metrics")
    f = get(f"/stores/{store_id}/funnel")
    h = get(f"/stores/{store_id}/heatmap")
    a = get(f"/stores/{store_id}/anomalies")

    with placeholder.container():
        st.title(f"🏪 {store_id}")
        st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')} · auto-refresh {refresh}s")

        if m:
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("Visitors",      m.get("unique_visitors", 0))
            c2.metric("Conversion",    f"{m.get('conversion_rate',0)*100:.1f}%")
            c3.metric("Converted",     m.get("converted_visitors", 0))
            c4.metric("Queue depth",   m.get("current_queue_depth", 0))
            c5.metric("Abandonment",   f"{m.get('abandonment_rate',0)*100:.1f}%")
            st.divider()

            left, right = st.columns(2)

            with left:
                st.subheader("Conversion Funnel")
                if f and f.get("funnel"):
                    fig = go.Figure(go.Funnel(
                        y=[s["label"] for s in f["funnel"]],
                        x=[s["count"] for s in f["funnel"]],
                        textinfo="value+percent previous",
                        marker=dict(color=["#7C3AED","#8B5CF6","#A78BFA","#C4B5FD"]),
                    ))
                    fig.update_layout(margin=dict(l=0,r=0,t=20,b=0),
                                      paper_bgcolor="rgba(0,0,0,0)", height=280)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No funnel data yet")

            with right:
                st.subheader("Zone Heatmap")
                if h and h.get("zones"):
                    df = pd.DataFrame(h["zones"])
                    fig = px.bar(df, x="zone_id", y="heat_score",
                                 color="heat_score",
                                 color_continuous_scale="Purples",
                                 labels={"heat_score":"Heat Score","zone_id":"Zone"})
                    fig.update_layout(margin=dict(l=0,r=0,t=20,b=0),
                                      paper_bgcolor="rgba(0,0,0,0)",
                                      coloraxis_showscale=False, height=280)
                    st.plotly_chart(fig, use_container_width=True)
                    if h.get("data_confidence") == "low":
                        st.warning("⚠️ Low confidence — fewer than 20 sessions")
                else:
                    st.info("No heatmap data yet")

            if m.get("zone_dwell"):
                st.subheader("Zone Dwell")
                df = pd.DataFrame(m["zone_dwell"])
                df["avg_dwell_s"] = (df["avg_dwell_ms"] / 1000).round(1)
                st.dataframe(df[["zone_id","visits","avg_dwell_s"]].rename(columns={
                    "zone_id":"Zone","visits":"Visits","avg_dwell_s":"Avg Dwell (s)"}),
                    use_container_width=True, hide_index=True)

            st.subheader("Active Anomalies")
            if a and a.get("anomalies"):
                for an in a["anomalies"]:
                    icons = {"CRITICAL":"🔴","WARN":"🟡","INFO":"🔵"}
                    sev   = an.get("severity","INFO")
                    st.warning(f"{icons.get(sev,'⚪')} **{an['anomaly_type']}** [{sev}]  \n"
                               f"{an['suggested_action']}")
            else:
                st.success("✅ No active anomalies")
        else:
            st.warning("⏳ Waiting for data — run `bash pipeline/run.sh` to start")

    time.sleep(refresh)
