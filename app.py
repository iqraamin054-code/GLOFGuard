
from datetime import date

import folium
import joblib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_folium import st_folium

MODEL_PATH = "artifacts/final_model.joblib"
DATA_PATH = "data/final/glofguard_training_data.csv"
DEFAULT_FEATURES = [
    "area", "temperature", "rainfall", "elevation",
    "distance_to_nearest_settlement_km",
    "annual_area_change_km2_per_year", "growth_data_available",
]
FEATURE_LABELS = {
    "area": "Lake area",
    "temperature": "Regional temperature",
    "rainfall": "Regional rainfall",
    "elevation": "Elevation",
    "distance_to_nearest_settlement_km": "Distance to nearest settlement",
    "annual_area_change_km2_per_year": "Lake growth rate",
    "growth_data_available": "Growth data available",
}
HIGH_PCT, MED_PCT = 0.95, 0.80  # top 5% = High, next 15% = Medium (display choice)
COLORS = {"High": "#e63946", "Medium": "#f4a261", "Low": "#2a9d8f"}
MAX_MARKERS = 3000

st.set_page_config(layout="wide", page_title="GLOFGuard", page_icon="🏔️")

st.markdown("""
<style>
    .block-container {padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1300px;}
    h1 {font-weight: 700; letter-spacing: -0.5px; margin-bottom: 0.1rem;}
    div[data-testid="stMetric"] {
        background: #f7f8fa; border: 1px solid #eceef1; border-radius: 10px;
        padding: 0.7rem 0.9rem;
    }
    div[data-testid="stMetricLabel"] {font-size: 0.78rem; color: #6b7280;}
    .band-badge {
        display: inline-block; padding: 0.22rem 0.7rem; border-radius: 999px;
        color: white; font-weight: 600; font-size: 0.85rem; letter-spacing: 0.2px;
    }
    .lake-card {
        background: #ffffff; border: 1px solid #eceef1; border-radius: 14px;
        padding: 1.1rem 1.3rem; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .why-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 0.4rem 0; border-bottom: 1px solid #f0f1f3; font-size: 0.92rem;
    }
    .why-row:last-child {border-bottom: none;}
    .why-up {color: #e63946; font-weight: 600;}
    .why-down {color: #2a9d8f; font-weight: 600;}
    section[data-testid="stSidebar"] {background: #fafafa;}
    .streamlit-expanderHeader {font-weight: 600;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data + model
@st.cache_resource
def load_model():
    obj = joblib.load(MODEL_PATH)
    if isinstance(obj, dict):
        return obj["pipeline"], obj.get("features", DEFAULT_FEATURES)
    return obj, DEFAULT_FEATURES


@st.cache_data
def load_scored():
    pipe, feats = load_model()
    df = pd.read_csv(DATA_PATH)
    df["score"] = pipe.predict_proba(df[feats])[:, 1]
    df["rank_pct"] = df["score"].rank(pct=True)
    df["band"] = np.where(df["rank_pct"] >= HIGH_PCT, "High",
                  np.where(df["rank_pct"] >= MED_PCT, "Medium", "Low"))
    df["rank"] = df["score"].rank(ascending=False, method="first").astype(int)
    return df


def what_if_score(pipe, feats, base_row, changes):
    row = base_row[feats].copy()
    for k, v in changes.items():
        row[k] = v
    return float(pipe.predict_proba(pd.DataFrame([row]))[:, 1][0])



# ------------------------------------------------------------ live conditions
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_power(lat, lon, start_year=2016):
    """Daily temperature and rainfall from NASA POWER (free, no key)."""
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "T2M,PRECTOTCORR", "community": "RE",
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start": f"{start_year}0101", "end": date.today().strftime("%Y%m%d"),
        "format": "JSON",
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    p = r.json()["properties"]["parameter"]
    out = pd.DataFrame({"temp_c": p["T2M"], "rain_mm": p["PRECTOTCORR"]})
    out.index = pd.to_datetime(out.index, format="%Y%m%d")
    out = out.replace(-999, np.nan).dropna(how="all")
    return out


def recent_anomaly(w, window=7, season_days=15):
    """Compare the latest `window` days with the same time of year in earlier years."""
    temp = w["temp_c"].rolling(window, min_periods=window).mean()
    rain = w["rain_mm"].rolling(window, min_periods=window).sum()
    last = w.index.max()
    doy = w.index.dayofyear
    diff = np.abs(doy - last.dayofyear)
    diff = np.minimum(diff, 365 - diff)
    hist = (diff <= season_days) & (w.index.year < last.year)
    res = {"last_date": last}
    for name, series in (("temp", temp), ("rain", rain)):
        cur = series.iloc[-1]
        ref = series[hist].dropna()
        res[name + "_now"] = cur
        res[name + "_pct"] = float((ref < cur).mean() * 100) if len(ref) else np.nan
        res[name + "_typical"] = float(ref.median()) if len(ref) else np.nan
    return res


def wording(pct, hi, lo):
    if np.isnan(pct):
        return "not enough history"
    if pct >= 90:
        return hi
    if pct <= 10:
        return lo
    return "within the normal range"


# ------------------------------------------------------------------------ UI
try:
    pipe, feats = load_model()
    df = load_scored()
except FileNotFoundError as e:
    st.error(f"Missing file: {e}. Run the model-saving cell in notebook 06 first.")
    st.stop()

st.title("🏔️ GLOFGuard")
st.caption("Glacial lake risk map for Northern Pakistan, with live weather for any lake you click.")

with st.sidebar:
    st.header("Filters")
    bands = st.multiselect("Risk band", ["High", "Medium", "Low"], default=["High", "Medium"])
    elev = st.slider("Elevation (m)", int(df.elevation.min()), int(df.elevation.max()),
                     (int(df.elevation.min()), int(df.elevation.max())))
    max_dist = st.slider("Max distance to nearest settlement (km)", 0.0,
                         float(np.ceil(df.distance_to_nearest_settlement_km.max())),
                         float(np.ceil(df.distance_to_nearest_settlement_km.max())))
    st.divider()
    st.caption("Bands: top 5% of scores = High, next 15% = Medium, rest = Low. "
               "These are display cut-offs on a ranking, not probabilities.")

view = df[
    df.band.isin(bands)
    & df.elevation.between(*elev)
    & (df.distance_to_nearest_settlement_km <= max_dist)
].sort_values("score", ascending=False)
shown = view.head(MAX_MARKERS)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Lakes shown", f"{len(view):,}")
c2.metric("High", int((view.band == "High").sum()))
c3.metric("Medium", int((view.band == "Medium").sum()))
c4.metric("Low", int((view.band == "Low").sum()))
if len(view) > MAX_MARKERS:
    st.info(f"Showing the {MAX_MARKERS:,} highest-ranked lakes of {len(view):,}. Narrow the filters to see the rest.")

st.write("")

# ============================================================== MAP + DETAIL
left, right = st.columns([3, 2], gap="medium")
with left:
    st.markdown("**Click a lake to see its details, why it's ranked that way, and live weather.**")
    if shown.empty:
        st.warning("No lakes match the current filters.")
    else:
        m = folium.Map(tiles="OpenStreetMap", control_scale=True)
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri World Imagery", name="Satellite view").add_to(m)
        m.fit_bounds([[shown.latitude.min(), shown.longitude.min()],
                      [shown.latitude.max(), shown.longitude.max()]])
        sel = st.session_state.get("sel")
        for r in shown.itertuples():
            is_sel = sel == r.sample_id
            folium.CircleMarker(
                [r.latitude, r.longitude],
                radius=10 if is_sel else 5,
                color="#1d1d1d" if is_sel else "#ffffff",
                weight=2 if is_sel else 1,
                fill=True, fill_color=COLORS[r.band], fill_opacity=0.9,
                tooltip=f"{r.band} risk - near {r.nearest_settlement_name}",
            ).add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)
        out = st_folium(m, height=560, use_container_width=True,
                        returned_objects=["last_object_clicked"], key="map")
        clk = out.get("last_object_clicked") if out else None
        if clk:
            d2 = (shown.latitude - clk["lat"]) ** 2 + (shown.longitude - clk["lng"]) ** 2
            new = int(shown.loc[d2.idxmin(), "sample_id"])
            if st.session_state.get("sel") != new:
                st.session_state["sel"] = new
                st.rerun()
        st.markdown(
            f"<span class='band-badge' style='background:{COLORS['High']}'>High</span>&nbsp;&nbsp;"
            f"<span class='band-badge' style='background:{COLORS['Medium']}'>Medium</span>&nbsp;&nbsp;"
            f"<span class='band-badge' style='background:{COLORS['Low']}'>Low</span>",
            unsafe_allow_html=True)

with right:
    sel = st.session_state.get("sel")
    if sel is None:
        st.info("👈 Click a lake on the map to get started.")
    else:
        row = df[df.sample_id == sel].iloc[0]
        st.markdown(f"""
        <div class="lake-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="margin:0;">{row.nearest_settlement_name}</h3>
                <span class="band-badge" style="background:{COLORS[row.band]}">{row.band}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        a, b = st.columns(2)
        a.metric("Elevation", f"{row.elevation:.0f} m")
        b.metric("Distance to settlement", f"{row.distance_to_nearest_settlement_km:.1f} km")

        st.markdown("#### 🌦️ Live recent conditions")
        try:
            with st.spinner("Fetching current weather..."):
                w = fetch_power(float(row.latitude), float(row.longitude))
            an = recent_anomaly(w)
            st.caption(f"Latest data: {an['last_date']:%d %b %Y}, compared with the same time of year in past years.")
            t, r_ = st.columns(2)
            t.metric("7-day avg temp", f"{an['temp_now']:.1f} °C",
                     f"typical {an['temp_typical']:.1f} °C", delta_color="off")
            r_.metric("7-day rainfall", f"{an['rain_now']:.1f} mm",
                      f"typical {an['rain_typical']:.1f} mm", delta_color="off")
            st.write(f"Temperature is **{wording(an['temp_pct'], 'unusually warm', 'unusually cold')}** for this time of year.")
            st.write(f"Rainfall is **{wording(an['rain_pct'], 'unusually wet', 'unusually dry')}** for this time of year.")
            fig = go.Figure()
            last90 = w.tail(90)
            fig.add_bar(x=last90.index, y=last90.rain_mm, name="Rain (mm)", yaxis="y2",
                        marker_color="#a8dadc", opacity=0.7)
            fig.add_scatter(x=last90.index, y=last90.temp_c, name="Temp (°C)", line=dict(color="#e76f51", width=2))
            fig.update_layout(height=230, margin=dict(l=0, r=0, t=10, b=0),
                              plot_bgcolor="white", paper_bgcolor="white",
                              yaxis=dict(title="°C", gridcolor="#f0f1f3"),
                              yaxis2=dict(title="mm", overlaying="y", side="right"),
                              legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:  # network down, API error, etc.
            st.warning(f"Could not fetch live weather ({type(e).__name__}). Check your internet connection.")

st.divider()

# ===================================================== OPTIONAL / ADVANCED
with st.expander("🎛️ What-if explorer - see how the ranking responds to changed conditions"):
    st.write("Pick a lake, change its inputs, and watch how the model's ranking responds. "
             "This shows what the model learned from the training data, not physical cause and effect.")
    default_id = st.session_state.get("sel") or int(df.sort_values("score").iloc[-1].sample_id)
    options = shown.sample_id.tolist() if not shown.empty else df.sample_id.tolist()
    if default_id not in options:
        default_id = options[0]
    name_lookup = df.set_index("sample_id")["nearest_settlement_name"]
    lake_id = st.selectbox("Choose a lake (from the lakes currently shown on the map)",
                           options, index=options.index(default_id),
                           format_func=lambda x: name_lookup.loc[x])
    base = df[df.sample_id == lake_id].iloc[0]
    s1, s2 = st.columns(2)
    with s1:
        n_elev = st.slider("Elevation (m)", int(df.elevation.min()), int(df.elevation.max()), int(base.elevation))
        n_dist = st.slider("Distance to settlement (km)", 0.1, 60.0,
                           float(np.clip(base.distance_to_nearest_settlement_km, 0.1, 60.0)))
        n_area = st.slider("Area (km²)", 0.0001, 1.0, float(np.clip(base.area, 0.0001, 1.0)), format="%.4f")
    with s2:
        n_temp = st.slider("Mean temperature (°C)", float(df.temperature.min()), float(df.temperature.max()),
                           float(base.temperature))
        n_rain = st.slider("Rainfall (dataset units)", float(df.rainfall.min()), float(df.rainfall.max()),
                           float(base.rainfall))
    changes = {"elevation": n_elev, "distance_to_nearest_settlement_km": n_dist, "area": n_area,
               "temperature": n_temp, "rainfall": n_rain}
    new_score = what_if_score(pipe, feats, base, changes)
    all_scores = np.sort(df.score.values)
    new_pct = np.searchsorted(all_scores, new_score) / len(all_scores)
    new_band = "High" if new_pct >= HIGH_PCT else "Medium" if new_pct >= MED_PCT else "Low"
    m1, m2, m3 = st.columns(3)
    m1.metric("Original percentile", f"{base.rank_pct * 100:.1f}", base.band, delta_color="off")
    m2.metric("What-if percentile", f"{new_pct * 100:.1f}", new_band, delta_color="off")
    m3.metric("Change", f"{(new_pct - base.rank_pct) * 100:+.1f} points")

    coefs = pipe.named_steps["clf"].coef_[0]
    imp = pd.DataFrame({"feature": [FEATURE_LABELS.get(f, f) for f in feats], "coefficient": coefs}).sort_values("coefficient")
    fig = go.Figure(go.Bar(x=imp.coefficient, y=imp.feature, orientation="h",
                           marker_color=[COLORS["High"] if c > 0 else "#457b9d" for c in imp.coefficient]))
    fig.update_layout(height=300, title="Logistic-regression coefficients (standardised inputs)",
                      margin=dict(l=0, r=0, t=40, b=0), plot_bgcolor="white", paper_bgcolor="white")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Positive = pushes the score up. Because inputs are standardised, sizes are comparable.")

with st.expander("📊 Model evidence and limitations"):
    st.subheader("3-fold geographic cross-validation")
    st.write("A single geographic train/test split can depend on which regions happen to enter the test set. "
             "Three-fold geographic cross-validation evaluates the models across multiple geographically "
             "separated splits to examine how consistently they perform on unseen regions.")
    cv = pd.DataFrame({
        "Model": ["Logistic Regression", "Random Forest", "SVM"],
        "ROC-AUC mean": [0.837, 0.779, 0.752], "ROC-AUC std": [0.032, 0.062, 0.042],
        "Recall mean": [0.725, 0.022, 0.381], "Recall std": [0.205, 0.016, 0.264]})
    st.dataframe(cv, hide_index=True, use_container_width=True)
    rs = pd.DataFrame({
        "Model": ["Logistic Regression", "Random Forest", "SVM"],
        "Random 80/20 split": [0.899, 0.988, 0.948],
        "Geographic split": [0.860, 0.802, 0.801]})
    fig = go.Figure()
    fig.add_bar(x=rs.Model, y=rs["Random 80/20 split"], name="Random split")
    fig.add_bar(x=rs.Model, y=rs["Geographic split"], name="Geographic split")
    fig.update_layout(barmode="group", height=320, yaxis_title="ROC-AUC", title="Random vs geographic evaluation",
                      plot_bgcolor="white", paper_bgcolor="white")
    st.plotly_chart(fig, use_container_width=True)
    st.success("Random Forest scores 0.988 on a random split but drops to 0.802 on unseen regions: "
               "it learned geography, not lake behaviour. Logistic Regression generalises best.")
    st.subheader("Limitations (read before interpreting the map)")
    st.markdown(
        "- **Labels are a proxy.** Positive = within 5 km of one of 53 reference coordinates (351 of 8,806 lakes). "
        "They are not verified GLOF events.\n"
        "- **Ranking, not prediction.** No dated, lake-level event labels with matching weather exist yet, "
        "so no validated forecast is possible. The live panel shows weather only; the model does not use it.\n"
        "- **Recall is unstable across regions** (0.725 ± 0.205), so treat bands as a triage aid.\n"
        "- **Growth data covers 36.3% of lakes**; missing values are imputed from the training set.\n"
        "- **Future work:** a verified event inventory, higher-resolution weather (e.g. ERA5-Land), and "
        "event-based time-aware validation.")