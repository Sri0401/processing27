-- Daily totals must equal the sum of hourly readings (no rows lost or double-counted).
select d.building_id, d.meter, d.total_kwh, h.total_kwh
from (select building_id, meter, sum(kwh) as total_kwh from {{ ref('fct_energy_daily') }} group by 1, 2) d
join (select building_id, meter, sum(kwh_clean) as total_kwh from {{ ref('fct_energy_hourly') }} group by 1, 2) h
    using (building_id, meter)
where abs(coalesce(d.total_kwh, 0) - coalesce(h.total_kwh, 0)) > 0.01
