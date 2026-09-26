import streamlit as st
import pandas as pd
import joblib
import folium
from folium.plugins import MarkerCluster
import streamlit.components.v1 as components

rf_model = joblib.load('artifacts/rf_model.joblib')
imputer = joblib.load('artifacts/imputer.joblib')
feature_cols = joblib.load('artifacts/feature_cols.joblib')

df = pd.read_csv('data/final/glofguard_training_data.csv')

st.set_page_config(layout="wide", page_title="GLOFGuard", page_icon="🏔️")
st.title("🏔️ GLOFGuard — Glacial Lake Risk Predictor")
st.markdown(f"**Northern Pakistan (Gilgit-Baltistan & KP)** — Risk predictions for **{len(df):,} glacial lakes**")

X = df[feature_cols].copy()
X['annual_area_change_km2_per_year'] = imputer.transform(X[['annual_area_change_km2_per_year']])

df['predicted_class'] = rf_model.predict(X)
df['probability_dangerous'] = rf_model.predict_proba(X)[:, 1]

st.subheader("📍 Interactive Risk Map")

# Fit map tightly to your actual lake region instead of a generic zoom
min_lat, max_lat = df['latitude'].min(), df['latitude'].max()
min_lon, max_lon = df['longitude'].min(), df['longitude'].max()

m = folium.Map(tiles="CartoDB positron", control_scale=True)
m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]])

# Group markers so the map isn't overwhelming when zoomed out
cluster = MarkerCluster(name="Lakes").add_to(m)

for _, row in df.iterrows():
    prob = row['probability_dangerous']
    if prob >= 0.7:
        color = '#d73027'  # red
        risk_label = "High"
    elif prob >= 0.3:
        color = '#fc8d59'  # orange
        risk_label = "Medium"
    else:
        color = '#4daf4a'  # green
        risk_label = "Low"

    popup_text = f"""
    <div style="font-family:Arial; font-size:13px;">
    <b>Lake #{row['sample_id']}</b><br>
    <b>Risk Level:</b> <span style="color:{color}"><b>{risk_label}</b></span><br>
    <b>Danger Probability:</b> {prob*100:.1f}%<br>
    <hr style="margin:4px 0">
    <b>Nearest Settlement:</b> {row['nearest_settlement_name']}<br>
    <b>Elevation:</b> {row['elevation']:.0f} m<br>
    <b>Distance to Settlement:</b> {row['distance_to_nearest_settlement_km']:.1f} km<br>
    <b>Area:</b> {row['area']:.4f} km²
    </div>
    """

    folium.CircleMarker(
        location=[row['latitude'], row['longitude']],
        radius=5,
        color=color,
        weight=1,
        fill=True,
        fill_color=color,
        fill_opacity=0.85,
        popup=folium.Popup(popup_text, max_width=280)
    ).add_to(cluster)

# Add a legend
legend_html = """
<div style="position: fixed; bottom: 30px; left: 30px; z-index:9999;
            background-color: white; padding: 10px 14px; border-radius: 6px;
            box-shadow: 0 0 6px rgba(0,0,0,0.3); font-family: Arial; font-size: 13px;">
<b>Risk Level</b><br>
<span style="color:#d73027;">●</span> High (≥70%)<br>
<span style="color:#fc8d59;">●</span> Medium (30–70%)<br>
<span style="color:#4daf4a;">●</span> Low (&lt;30%)
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

components.html(m._repr_html_(), height=650)

st.subheader("📊 Summary")
col1, col2, col3 = st.columns(3)
col1.metric("🔴 High Risk", int((df['probability_dangerous'] >= 0.7).sum()))
col2.metric("🟠 Medium Risk", int(((df['probability_dangerous'] >= 0.3) & (df['probability_dangerous'] < 0.7)).sum()))
col3.metric("🟢 Low Risk", int((df['probability_dangerous'] < 0.3).sum()))