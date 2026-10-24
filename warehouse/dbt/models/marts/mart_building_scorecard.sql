{#-
  One row per building, joining energy, asset condition, maintenance and accessibility.
  The single view a Head of Estates needs to decide where the next pound goes.
-#}
{%- set ref_year = var('asset_reference_year') -%}

with energy as (
    select
        building_id,
        sum(kwh)                                                    as energy_kwh,
        sum(cost_gbp)                                               as energy_cost_gbp,
        sum(tco2e)                                                  as energy_tco2e,
        max(eui_vs_peer_median) filter (where meter = 'electricity') as electricity_eui_vs_peer
    from {{ ref('mart_building_energy_kpis') }}
    group by 1
),

ooh as (
    select building_id, sum(savings_gbp) as ooh_savings_gbp, sum(savings_tco2e) as ooh_savings_tco2e
    from {{ ref('mart_out_of_hours_opportunity') }}
    group by 1
),

assets as (
    select
        building_id,
        count(*)                                                    as assets,
        count(*) filter (where condition_grade in ('C', 'D'))       as assets_poor_or_worse,
        count(*) filter (where investment_priority like '1%')       as assets_replace_now,
        sum(backlog_cost_gbp)                                       as backlog_cost_gbp
    from {{ ref('mart_asset_condition') }}
    group by 1
),

maintenance as (
    select
        building_id,
        sum(total_cost_gbp)                                         as maintenance_cost_gbp,
        sum(total_cost_gbp) filter (where work_type = 'reactive')
            / nullif(sum(total_cost_gbp), 0)                        as reactive_cost_share,
        avg(is_sla_met::int)                                        as sla_compliance
    from {{ ref('fct_work_orders') }}
    where extract(year from raised_date) = {{ ref_year }}
    group by 1
)

select
    b.building_id,
    b.building_name,
    b.space_usage,
    b.floor_area_m2,
    b.year_built,
    b.age_band,
    e.energy_kwh,
    e.energy_cost_gbp,
    e.energy_tco2e,
    e.electricity_eui_vs_peer,
    coalesce(o.ooh_savings_gbp, 0)                                  as ooh_savings_gbp,
    coalesce(o.ooh_savings_tco2e, 0)                                as ooh_savings_tco2e,
    a.assets,
    a.assets_poor_or_worse,
    a.assets_replace_now,
    a.backlog_cost_gbp,
    a.backlog_cost_gbp / nullif(b.floor_area_m2, 0)                 as backlog_gbp_per_m2,
    m.maintenance_cost_gbp,
    m.maintenance_cost_gbp / nullif(b.floor_area_m2, 0)             as maintenance_gbp_per_m2,
    m.reactive_cost_share,
    m.sla_compliance,
    x.compliance_pct                                                as accessibility_compliance_pct,
    x.rag_status                                                    as accessibility_rag,
    x.remediation_cost_gbp                                          as accessibility_remediation_gbp
from {{ ref('dim_building') }} b
left join energy e using (building_id)
left join ooh o using (building_id)
left join assets a using (building_id)
left join maintenance m using (building_id)
left join {{ ref('mart_accessibility_compliance') }} x using (building_id)
