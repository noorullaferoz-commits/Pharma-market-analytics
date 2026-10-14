-- =====================================================================
-- Pharma Market Analytics: KPI queries
-- Dataset: SAMPLE / SYNTHETIC data (fictional brands and companies)
-- Works in SQLite, PostgreSQL and MySQL 8+ (window functions required)
-- Tables: fact_sales, dim_brand, dim_doctor_universe (load the CSVs first)
-- =====================================================================

-- 1. Market size by therapy area and year (INR crore)
SELECT b.therapy_area,
       SUBSTR(f.month, 1, 4)                 AS year,
       ROUND(SUM(f.value_inr) / 1e7, 2)      AS market_value_cr
FROM fact_sales f
JOIN dim_brand b ON b.brand_id = f.brand_id
GROUP BY b.therapy_area, SUBSTR(f.month, 1, 4)
ORDER BY b.therapy_area, year;

-- 2. Market share by brand for 2025
WITH brand_sales AS (
    SELECT b.therapy_area, b.brand, SUM(f.value_inr) AS sales
    FROM fact_sales f
    JOIN dim_brand b ON b.brand_id = f.brand_id
    WHERE SUBSTR(f.month, 1, 4) = '2025'
    GROUP BY b.therapy_area, b.brand
)
SELECT therapy_area,
       brand,
       ROUND(sales / 1e7, 2)                                                    AS sales_cr,
       ROUND(100.0 * sales / SUM(sales) OVER (PARTITION BY therapy_area), 1)    AS market_share_pct
FROM brand_sales
ORDER BY therapy_area, market_share_pct DESC;

-- 3. Brand growth, market growth and Evolution Index (2025 vs 2024)
--    Evolution Index = (1 + brand growth) / (1 + market growth) x 100
--    EI > 100: brand growing faster than its market (gaining share)
WITH yearly AS (
    SELECT b.therapy_area, b.brand,
           SUM(CASE WHEN SUBSTR(f.month, 1, 4) = '2024' THEN f.value_inr ELSE 0 END) AS s2024,
           SUM(CASE WHEN SUBSTR(f.month, 1, 4) = '2025' THEN f.value_inr ELSE 0 END) AS s2025
    FROM fact_sales f
    JOIN dim_brand b ON b.brand_id = f.brand_id
    GROUP BY b.therapy_area, b.brand
),
with_market AS (
    SELECT *,
           SUM(s2024) OVER (PARTITION BY therapy_area) AS m2024,
           SUM(s2025) OVER (PARTITION BY therapy_area) AS m2025
    FROM yearly
)
SELECT therapy_area,
       brand,
       ROUND(100.0 * (s2025 - s2024) / s2024, 1)                       AS brand_growth_pct,
       ROUND(100.0 * (m2025 - m2024) / m2024, 1)                       AS market_growth_pct,
       ROUND(100.0 * (1.0 * s2025 / s2024) / (1.0 * m2025 / m2024), 1) AS evolution_index
FROM with_market
ORDER BY therapy_area, evolution_index DESC;

-- 4. Prescriber penetration rate by brand (2025, average month)
--    Penetration = prescribing doctors / target doctor universe in that therapy area
WITH monthly AS (
    SELECT b.therapy_area, b.brand, f.month, SUM(f.prescribing_doctors) AS prescribers
    FROM fact_sales f
    JOIN dim_brand b ON b.brand_id = f.brand_id
    WHERE SUBSTR(f.month, 1, 4) = '2025'
    GROUP BY b.therapy_area, b.brand, f.month
),
universe AS (
    SELECT therapy_area, SUM(target_doctors) AS target_doctors
    FROM dim_doctor_universe
    GROUP BY therapy_area
)
SELECT m.therapy_area,
       m.brand,
       ROUND(AVG(m.prescribers), 0)                          AS avg_monthly_prescribers,
       u.target_doctors,
       ROUND(100.0 * AVG(m.prescribers) / u.target_doctors, 1) AS penetration_pct
FROM monthly m
JOIN universe u ON u.therapy_area = m.therapy_area
GROUP BY m.therapy_area, m.brand, u.target_doctors
ORDER BY m.therapy_area, penetration_pct DESC;

-- 5. Penetration by region for one brand (change the brand name)
SELECT f.region,
       ROUND(AVG(f.prescribing_doctors), 0)                         AS avg_prescribers,
       d.target_doctors,
       ROUND(100.0 * AVG(f.prescribing_doctors) / d.target_doctors, 1) AS penetration_pct
FROM fact_sales f
JOIN dim_brand b           ON b.brand_id = f.brand_id
JOIN dim_doctor_universe d ON d.ta_region_key = f.ta_region_key
WHERE b.brand = 'Dapaflo' AND SUBSTR(f.month, 1, 4) = '2025'
GROUP BY f.region, d.target_doctors
ORDER BY penetration_pct DESC;

-- 6. Monthly market share trend (for a line chart)
WITH m AS (
    SELECT b.therapy_area, b.brand, f.month, SUM(f.value_inr) AS sales
    FROM fact_sales f
    JOIN dim_brand b ON b.brand_id = f.brand_id
    GROUP BY b.therapy_area, b.brand, f.month
)
SELECT therapy_area, brand, month,
       ROUND(100.0 * sales / SUM(sales) OVER (PARTITION BY therapy_area, month), 1) AS market_share_pct
FROM m
ORDER BY therapy_area, brand, month;
