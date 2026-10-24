-- Readings flagged by data-quality rules, with the expected value, for triage by the BMS team.
select
    f.building_id,
    b.building_name,
    b.space_usage,
    f.meter,
    f.reading_ts,
    case when f.is_spike then 'spike' else 'stuck_sensor' end      as anomaly_type,
    f.kwh                                                           as observed_kwh,
    f.trailing_mean_kwh                                             as expected_kwh,
    f.kwh - f.trailing_mean_kwh                                     as deviation_kwh,
    f.load_source
from {{ ref('int_meter_readings_flagged') }} f
join {{ ref('dim_building') }} b using (building_id)
where f.is_spike or f.is_stuck
