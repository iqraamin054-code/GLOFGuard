# 🏔️ GLOFGuard

**Glacial lake risk ranking for Northern Pakistan, with live weather for any lake.**

## 🚀 Tech Stack

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white) ![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikitlearn&logoColor=white) ![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white) ![NASA POWER](https://img.shields.io/badge/NASA_POWER_API-0B3D91?style=for-the-badge) ![Git](https://img.shields.io/badge/Git-F05032?style=for-the-badge&logo=git&logoColor=white) ![GitHub](https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white)

---

GLOFGuard ranks about 8,800 glacial lakes in Northern Pakistan by their relative risk of a Glacial Lake Outburst Flood (GLOF). An interactive Streamlit map shows each lake's risk band, why it is ranked that way, and recent weather compared with past years.

> ⚠️ This is a **risk ranking, not a prediction**. Labels are proxies, not verified GLOF events. See [Limitations](#limitations).

---

## ✨ Features

- 🗺️ **Interactive map** of 8,806 lakes, coloured High / Medium / Low
- 🔍 **Filters** by risk band, elevation and distance to the nearest settlement
- 📊 **Lake details**: score, rank and the factors driving it
- 🌦️ **Live weather**: last 7 days of temperature and rainfall from NASA POWER (free, no API key), compared with the same season in past years
- 🧪 **What-if panel** to see how the score changes when inputs change
- 📈 **Model insights**: coefficients, cross-validation results and limitations

---

## 🧠 Method

**Model:** Logistic Regression (scikit-learn pipeline with imputation and scaling)

**Features**

| Feature | Description |
|---|---|
| `area` | Lake area |
| `temperature` | Regional temperature |
| `rainfall` | Regional rainfall |
| `elevation` | Lake elevation (m) |
| `distance_to_nearest_settlement_km` | Distance to the nearest village (km) |
| `annual_area_change_km2_per_year` | Lake growth rate |
| `growth_data_available` | Whether growth data exists for the lake |

**Risk bands:** top 5% of scores = High, next 15% = Medium, rest = Low.

### Why Logistic Regression?
Models were tested on both a random split and a **geographic split** (unseen regions):

| Model | Random 80/20 (AUC) | Geographic split (AUC) | 3-fold geographic CV (AUC) |
|---|---|---|---|
| **Logistic Regression** | 0.899 | **0.860** | **0.837 ± 0.032** |
| Random Forest | 0.988 | 0.802 | 0.779 ± 0.062 |
| SVM | 0.948 | 0.801 | 0.752 ± 0.042 |

Random Forest looked best on a random split but dropped sharply on unseen regions: it learned geography, not lake behaviour. Logistic Regression has the highest AUC on unseen regions, but its precision is low (0.23 on the geographic split), so treat it as a ranking, not a warning.

---

## 📁 Project Structure

```
GLOFGuard/
├── app.py                  # Streamlit app
├── requirements.txt        # pip dependencies
├── environment.yml         # conda environment (notebooks)
├── artifacts/              # trained model and feature list
├── data/
│   ├── raw/                # original datasets
│   ├── processed/          # cleaned intermediate files
│   └── final/              # final training dataset
└── notebooks/
    ├── 01_data_exploration.ipynb
    ├── 02_settlements_processing.ipynb
    ├── 03_elevation_processing.ipynb
    ├── 04_growth_rate_processing.ipynb
    ├── 05_final_dataset.ipynb
    ├── 06_model_training.ipynb
    └── 07_event_lake_matching.ipynb
```

---

## 🚀 How to Run

```bash
git clone https://github.com/iqraamin054-code/GLOFGuard.git
cd GLOFGuard
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501 in your browser.

---

## 📚 Data Sources

- Glacial lake inventory for Northern Pakistan
- OpenStreetMap settlements (distance to nearest village)
- Elevation data
- Lake area change (growth rate) data
- NASA POWER API for live daily temperature and rainfall
- HMAGLOFDB (High Mountain Asia GLOF database) for event matching

---

## <a name="limitations"></a>⚠️ Limitations

- **Labels are a proxy.** Positive = within 5 km of one of 53 reference GLOF coordinates (351 of 8,806 lakes), not verified events.
- **Ranking, not prediction.** No dated, lake-level event data exists yet, so no validated forecast is possible. The live weather panel is for context; the model does not use it.
- **Recall varies across regions** (0.725 ± 0.205), so treat bands as a triage aid.
- **Growth data covers 36.3% of lakes**; missing values are imputed from the training set.
- Positive lakes tend to be lower and closer to villages, so the model may partly reflect where events are recorded.
- **Event check:** matching against the HMAGLOFDB database (151 events, 14 to 24 matched lakes) shows no better than chance on the event lakes at 1, 2 and 3 km. The test is too small to say more.

## 🔭 Future Work

- A verified GLOF event inventory
- Higher-resolution weather data (e.g. ERA5-Land)
- Event-based, time-aware validation on data not used for labelling

---

## 👩‍💻 Author

**Iqra Amin**, Software Engineering student
GitHub: [@iqraamin054-code](https://github.com/iqraamin054-code)
