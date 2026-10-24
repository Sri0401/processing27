-- Grain: building x month x asset class. Reactive vs planned split drives the maintenance strategy KPI.
select
    w.building_id,
    w.building_name,
    w.space_usage,
    w.raised_month                                                      as month_start,
    w.asset_class,
    count(*)                                                            as work_orders,
    count(*) filter (where w.work_type = 'reactive')                    as reactive_work_orders,
    sum(w.total_cost_gbp)                                               as total_cost_gbp,
    coalesce(sum(w.total_cost_gbp) filter (where w.work_type = 'reactive'), 0) as reactive_cost_gbp,
    coalesce(sum(w.total_cost_gbp) filter (where w.work_type = 'planned'), 0)  as planned_cost_gbp,
    count(*) filter (where w.is_sla_met)                                as sla_met,
    count(*) filter (where w.is_sla_met is not null)                    as sla_measured,
    sum(w.total_cost_gbp) / nullif(max(b.floor_area_m2), 0)            as cost_gbp_per_m2
from {{ ref('fct_work_orders') }} w
join {{ ref('dim_building') }} b using (building_id)
group by 1, 2, 3, 4, 5
