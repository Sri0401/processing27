-- Grain: building x meter. Completeness and quality of telemetry - the "can we trust it?" view.
select
    f.building_id,
    b.building_name,
    f.meter,
    min(f.reading_ts)                                               as first_reading_ts,
    max(f.reading_ts)                                               as last_reading_ts,
    max(f.ingested_at)                                              as last_ingested_at,
    count(*)                                                        as readings,
    (extract(epoch from max(f.reading_ts) - min(f.reading_ts)) / 3600)::int + 1 as expected_readings,
    count(*)::numeric
        / ((extract(epoch from max(f.reading_ts) - min(f.reading_ts)) / 3600) + 1) as completeness,
    count(*) filter (where f.is_spike)                              as spike_readings,
    count(*) filter (where f.is_stuck)                              as stuck_readings,
    count(*) filter (where f.load_source = 'stream')                as streamed_readings
from {{ ref('int_meter_readings_flagged') }} f
join {{ ref('dim_building') }} b using (building_id)
group by f.building_id, b.building_name, f.meter
