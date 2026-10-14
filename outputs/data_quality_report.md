# Data quality report

| Check | Status | Detail |
|---|---|---|
| fact_sales: required columns | PASS | all present |
| fact_sales: no null values | PASS | 0 nulls |
| dim_brand: required columns | PASS | all present |
| dim_brand: no null values | PASS | 0 nulls |
| dim_doctor_universe: required columns | PASS | all present |
| dim_doctor_universe: no null values | PASS | 0 nulls |
| fact_sales: no negative values | PASS | 0 negative cells |
| fact_sales: one row per month-brand-region | PASS | 0 duplicates |
| fact_sales -> dim_brand keys match | PASS | 0 orphan rows |
| fact_sales -> doctor universe keys match | PASS | 0 orphan rows |
| continuous monthly coverage | PASS | Jan 2024 to Dec 2025, 24 months |
| prescribers never exceed target universe | PASS | 0 rows over universe |
