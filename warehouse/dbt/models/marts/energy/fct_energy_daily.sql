-- Grain: building x meter x local day. Heating degree days (base 15.5 C) for weather normalisation.
with weather_daily as (
    select
        (weather_ts at time zone 'Europe/London')::date        as local_date,
        avg(air_temperature_c)                                 as mean_air_temperature_c
    from {{ ref('stg_weather') }}
    group by 1
)

select
    h.building_id,
    h.meter,
    h.local_date,
    sum(h.kwh_clean)                                           as kwh,
    sum(h.kwh_clean) filter (where h.is_occupied)              as occupied_kwh,
    sum(h.kwh_clean) filter (where not h.is_occupied)          as unoccupied_kwh,
    sum(h.cost_gbp_clean)                                      as cost_gbp,
    sum(h.kgco2e_clean)                                        as kgco2e,
    count(*)                                                   as readings,
    count(*) filter (where not h.is_valid)                     as flagged_readings,
    bool_or(h.is_term_time)                                    as is_term_time,
    w.mean_air_temperature_c,
    greatest(15.5 - w.mean_air_temperature_c, 0)               as heating_degree_days
from {{ ref('fct_energy_hourly') }} h
left join weather_daily w using (local_date)
group by h.building_id, h.meter, h.local_date, w.mean_air_temperature_c
