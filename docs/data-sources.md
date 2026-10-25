# Data sources and assumptions

## Real data: Building Data Genome Project 2 (BDG2)

- Miller, C., Kathirgamanathan, A., Picchetti, B. et al. *The Building Data Genome Project 2, energy meter data from the ASHRAE Great Energy Predictor III competition.* Scientific Data 7, 368 (2020). Licence: **CC BY 4.0**.
- Files used (`make data`): `metadata.csv`, `weather.csv`, `meters/cleaned/electricity_cleaned.csv`, `meters/cleaned/gas_cleaned.csv`.
- Site **"Lamb"** (Europe/London): 147 buildings (83 education), 146 electricity and 119 gas meters, hourly readings for 2016–2017.
- Building names in BDG2 are pseudonyms (e.g. `Lamb_education_Terina`). There is no personal data.

### Known quirks (and how the platform handles them)

| Quirk | Handling |
|---|---|
| "Cleaned" files contain long constant runs (gap-fill) | Flagged as stuck sensors (ADR 0004) |
| Site-wide zero or empty weeks in 2017 | Flagged as outages; daily charts show gaps |
| A few buildings have implausible energy intensity (> 1,000 kWh/m²) | Shown, with a note that sub-metering or floor-area errors are likely; worth checking with the metering contractor |
| Timestamps in local standard time | Treated as UTC for a GMT site (ADR 0003) |

## Synthetic data: estates systems

There is no open dataset of a university's asset register, CAFM work orders, condition surveys or access audits. `src/smart_campus/synthetic.py` generates them deterministically (seed 7), keyed to the real BDG2 buildings, so they can be joined to real energy data.

| Table | Rows | Key assumptions |
|---|---|---|
| `asset_register` | 1,136 | 9 asset classes; indicative lives in the style of CIBSE Guide M (boilers 20 y, lifts 25 y, roofs 30 y …); assets replaced in cycles of 95–140% of their expected life since the build year; replacement cost scaled by floor area |
| `condition_survey` | 2,272 | Annual surveys (2016, 2017); grade from age ÷ life, plus noise: A < 0.3, B < 0.7, C < 1.05, else D; grades never improve without replacement |
| `work_order` | 9,733 | Reactive failure rate rises steeply after 60% of life; planned PPM visits per class; P1–P4 priority mix, gamma-distributed completion times, log-normal costs |
| `accessibility_audit` | 1,470 | 10 checks in the style of BS 8300 / Approved Document M; pass probability higher for buildings designed after the 2004 accessibility duties; lift and refuge checks apply only to multi-storey buildings, Changing Places only to buildings > 5,000 m² |

These are **plausible, not real**. They demonstrate the models and dashboards; any conclusions about maintenance or accessibility apply to the method, not to a real estate.

## Reference data (dbt seeds, editable by the business)

| Seed | Owner (intended) | Note |
|---|---|---|
| `energy_tariffs.csv` | Finance | **Assumed** non-domestic unit rates; replace with the supply contract |
| `carbon_factors.csv` | Sustainability | UK Government GHG conversion factors; check the values against the published year before external reporting |
| `occupancy_schedules.csv` | Facilities | Opening hours by space use |
| `academic_terms.csv` | Timetabling | Term dates 2015/16–2017/18 |
| `condition_grades.csv` | Estates | A–D scale and backlog factors |
| `priority_slas.csv` | Facilities | Response targets by priority |
