{#-
  Grain: one row per building x meter x hour.
  Adds the business context needed to turn kWh into decisions: local time, occupancy
  (per space-use schedule), academic term, cost (tariff) and carbon (UK conversion factors).
  `*_clean` measures exclude readings flagged as spikes or stuck sensors.
-#}
{{
  config(
    indexes=[
      {'columns': ['building_id', 'reading_ts']},
      {'columns': ['local_date']},
    ],
  )
}}

with readings as (
    select
        *,
        reading_ts at time zone 'Europe/London' as local_ts
    from {{ ref('int_meter_readings_flagged') }}
),

enriched as (
    select
        r.building_id,
        r.meter,
        r.reading_ts,
        r.local_ts,
        r.local_ts::date                                    as local_date,
        extract(hour from r.local_ts)::int                  as local_hour,
        extract(isodow from r.local_ts) in (6, 7)           as is_weekend,
        r.kwh,
        r.is_spike,
        r.is_stuck,
        not (r.is_spike or r.is_stuck)                      as is_valid,
        r.load_source,
        o.weekday_start_hour,
        o.weekday_end_hour,
        o.weekend_start_hour,
        o.weekend_end_hour
    from readings r
    join {{ ref('stg_buildings') }} b using (building_id)
    left join {{ ref('occupancy_schedules') }} o on o.space_usage = b.space_usage
)

select
    e.building_id,
    e.meter,
    e.reading_ts,
    e.local_date,
    e.local_hour,
    e.is_weekend,
    t.term is not null                                      as is_term_time,
    case
        when e.is_weekend then e.local_hour >= e.weekend_start_hour and e.local_hour < e.weekend_end_hour
        else e.local_hour >= e.weekday_start_hour and e.local_hour < e.weekday_end_hour
    end                                                     as is_occupied,
    e.kwh,
    e.is_spike,
    e.is_stuck,
    e.is_valid,
    case when e.is_valid then e.kwh end                     as kwh_clean,
    case when e.is_valid then e.kwh * tar.unit_rate_gbp_per_kwh end as cost_gbp_clean,
    case when e.is_valid then e.kwh * cf.kgco2e_per_kwh end as kgco2e_clean,
    e.load_source
from enriched e
left join {{ ref('academic_terms') }} t
    on e.local_date between t.start_date and t.end_date
left join {{ ref('energy_tariffs') }} tar
    on tar.meter = e.meter and e.local_date between tar.valid_from and tar.valid_to
left join {{ ref('carbon_factors') }} cf
    on cf.meter = e.meter and cf.year = extract(year from e.local_date)
