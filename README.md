# 🏔️ GLOFGuard

**Glacial Lake Risk Ranking for Northern Pakistan, with Weather Insights**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask"/>
  <img src="https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikitlearn&logoColor=white" alt="scikit-learn"/>
  <img src="https://img.shields.io/badge/Pandas-150458?style=for-the-badge&logo=pandas&logoColor=white" alt="Pandas"/>
  <img src="https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white" alt="HTML5"/>
  <img src="https://img.shields.io/badge/CSS3-1572B6?style=for-the-badge&logo=css3&logoColor=white" alt="CSS3"/>
  <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black" alt="JavaScript"/>
  <img src="https://img.shields.io/badge/NASA_POWER_API-0B3D91?style=for-the-badge" alt="NASA POWER API"/>
  <img src="https://img.shields.io/badge/Git-F05032?style=for-the-badge&logo=git&logoColor=white" alt="Git"/>
  <img src="https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub"/>
</p>

---

GLOFGuard ranks approximately **8,806 glacial lakes in Northern Pakistan** by their relative risk of a Glacial Lake Outburst Flood (GLOF).

The project combines machine learning, an interactive dashboard, and weather information to help users explore lake risk rankings, understand risk factors, and view weather conditions.

> ⚠️ **Risk ranking, not a prediction.** The model's labels are proxies, not verified GLOF events. See [Limitations](#limitations).

---

## ✨ Features

- 🗺️ **Interactive dashboard** displaying 8,806 glacial lakes and their High, Medium, and Low risk bands.
- 🔍 **Lake filtering** by risk band, elevation, and distance to the nearest settlement.
- 📊 **Lake details** including risk score, ranking, and contributing factors.
- 🌦️ **Weather insights** with temperature and rainfall information.
- 🧪 **Weather What-If panel** to explore how changing rainfall and temperature inputs affect the simulated alert.
- 🤖 **Machine learning integration** using a trained Logistic Regression model served through Flask.
- 📈 **Model insights** including model evaluation, coefficients, and limitations.

---

## 🧠 Machine Learning Method

**Model:** Logistic Regression using a scikit-learn pipeline with imputation and scaling.

### Features

| Feature | Description |
|---|---|
| `area` | Lake area |
| `temperature` | Regional temperature |
| `rainfall` | Regional rainfall |
| `elevation` | Lake elevation (m) |
| `distance_to_nearest_settlement_km` | Distance to the nearest village (km) |
| `annual_area_change_km2_per_year` | Lake growth rate |
| `growth_data_available` | Whether growth data exists for the lake |

### Risk Bands

- **High:** Top 5% of model risk scores.
- **Medium:** Next 15%.
- **Low:** Remaining 80%.

### Why Logistic Regression?

Models were evaluated using both a random split and a geographic split to assess performance on unseen regions.

| Model | Random 80/20 (AUC) | Geographic Split (AUC) | 3-Fold Geographic CV (AUC) |
|---|---:|---:|---:|
| **Logistic Regression** | 0.899 | **0.860** | **0.837 ± 0.032** |
| Random Forest | 0.988 | 0.802 | 0.779 ± 0.062 |
| SVM | 0.948 | 0.801 | 0.752 ± 0.042 |

Random Forest performed best on the random split but its performance dropped on unseen geographic regions. Logistic Regression achieved the highest AUC on the geographic split.

However, its precision was low (0.23 on the geographic split), so the risk bands should be treated as a ranking and triage aid rather than a warning or confirmed prediction.

---

## 🔄 How the Application Works

1. **Dataset:** The processed lake dataset provides the features used for risk assessment.
2. **Machine Learning:** The trained Logistic Regression model calculates risk scores for the lakes.
3. **Flask Backend:** Flask loads the trained model, processes predictions, and provides data through application routes and API endpoints.
4. **Dashboard:** The frontend displays lake information, risk rankings, and model results.
5. **User Interaction:** Users can explore lake details, filters, weather information, and the What-If panel.

---

## 📁 Project Structure

```text
GLOFGuard/
│
├── app.py                         # Flask backend and ML integration
├── requirements.txt               # Python dependencies
│
├── artifacts/
│   └── final_model.joblib         # Trained ML model
│
├── data/
│   ├── raw/                       # Original datasets
│   ├── processed/                 # Cleaned intermediate datasets
│   └── final/
│       └── glofguard_training_data.csv
│
├── templates/
│   └── GLOFGuard_Project.html     # Dashboard frontend
│
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

Follow these steps to run GLOFGuard locally.

### 1. Clone the repository

```bash
git clone https://github.com/iqraamin054-code/GLOFGuard.git
```

### 2. Open the project folder

```bash
cd GLOFGuard
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Start the Flask application

```bash
python app.py
```
---

## 📚 Data Sources

- Glacial lake inventory for Northern Pakistan.
- OpenStreetMap settlements for distance-to-settlement calculations.
- Elevation data.
- Lake area change and growth-rate data.
- NASA POWER API for weather information.
- HMAGLOFDB (High Mountain Asia GLOF Database) for event matching.

---

## <a name="limitations"></a>⚠️ Limitations

- **Labels are a proxy.** Positive labels represent lakes within 5 km of one of 53 reference GLOF coordinates (351 of 8,806 lakes), not verified GLOF events.
- **Ranking, not prediction.** No dated, lake-level event data currently exists to validate a true forecasting model. The live weather panel provides context; the model does not use live weather inputs.
- **Recall varies across regions** (0.725 ± 0.205), so risk bands should be treated as a triage aid.
- **Growth data covers 36.3% of lakes.** Missing values are imputed using training data.
- Positive lakes tend to be lower and closer to villages, meaning the model may partly reflect where events have been recorded.
- **Event check:** Matching against HMAGLOFDB (151 events, 14 to 24 matched lakes) showed no better than chance on event lakes at 1, 2, and 3 km. The test is too small to draw stronger conclusions.

---

## 🔭 Future Work

- Build a verified GLOF event inventory.
- Integrate higher-resolution weather data, such as ERA5-Land.
- Perform event-based, time-aware validation using data not involved in labelling.
- Improve model evaluation with additional verified event records.
