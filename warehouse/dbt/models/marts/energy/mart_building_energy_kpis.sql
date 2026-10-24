{#-
  Grain: building x meter, for the KPI year.
  EUI (energy use intensity, kWh/m2/yr) is benchmarked against buildings with the same
  space use. Buildings with < 90% data coverage get no EUI rather than a misleading one.
  Baseload: 10th percentile of hourly kWh, i.e. the average kW the building never drops below.
-#}
{%- set kpi_year = var('kpi_year') -%}

with hourly as (
    select * from {{ ref('fct_energy_hourly') }}
    where local_date >= '{{ kpi_year }}-01-01' and local_date < '{{ kpi_year + 1 }}-01-01'
),

per_meter as (
    select
        building_id,
        meter,
        count(*)                                                    as hours_reported,
        count(*) filter (where not is_valid)                        as hours_flagged,
        sum(kwh_clean)                                              as kwh,
        sum(kwh_clean) filter (where not is_occupied)               as unoccupied_kwh,
        sum(kwh_clean) filter (where is_occupied)                   as occupied_kwh,
        sum(cost_gbp_clean)                                         as cost_gbp,
        sum(kgco2e_clean) / 1000.0                                  as tco2e,
        percentile_cont(0.10) within group (order by kwh_clean)     as baseload_kw,
        percentile_cont(0.99) within group (order by kwh_clean)     as peak_kw
    from hourly
    group by 1, 2
),

kpis as (
    select
        m.*,
        b.building_name,
        b.space_usage,
        b.floor_area_m2,
        m.hours_reported::numeric / {{ 366 if kpi_year % 4 == 0 else 365 }} / 24   as data_coverage,
        m.unoccupied_kwh / nullif(m.kwh, 0)                         as unoccupied_share,
        m.baseload_kw / nullif(m.peak_kw, 0)                        as baseload_to_peak_ratio
    from per_meter m
    join {{ ref('dim_building') }} b using (building_id)
),

with_eui as (
    select
        *,
        case when data_coverage >= 0.9 and kwh > 0 then kwh / nullif(floor_area_m2, 0) end as eui_kwh_m2
    from kpis
),

peers as (
    select
        space_usage,
        meter,
        count(eui_kwh_m2)                                           as peer_count,
        percentile_cont(0.5) within group (order by eui_kwh_m2)     as peer_median_eui_kwh_m2
    from with_eui
    group by 1, 2
)

select
    e.building_id,
    e.building_name,
    e.space_usage,
    e.meter,
    {{ kpi_year }}                                                  as kpi_year,
    e.floor_area_m2,
    e.data_coverage,
    e.hours_flagged,
    e.kwh,
    e.occupied_kwh,
    e.unoccupied_kwh,
    e.unoccupied_share,
    e.cost_gbp,
    e.tco2e,
    e.baseload_kw,
    e.peak_kw,
    e.baseload_to_peak_ratio,
    e.eui_kwh_m2,
    p.peer_count,
    p.peer_median_eui_kwh_m2,
    e.eui_kwh_m2 / nullif(p.peer_median_eui_kwh_m2, 0) - 1          as eui_vs_peer_median,
    rank() over (partition by e.meter order by e.eui_kwh_m2 desc nulls last) as eui_rank_on_campus
from with_eui e
left join peers p using (space_usage, meter)
