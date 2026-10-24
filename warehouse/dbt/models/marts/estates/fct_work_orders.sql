-- Grain: one row per work order, with SLA outcome.
select
    w.work_order_id,
    w.asset_id,
    w.building_id,
    b.building_name,
    b.space_usage,
    w.asset_class,
    w.work_type,
    w.priority,
    s.label                                                     as priority_label,
    w.description,
    w.raised_at,
    w.completed_at,
    (w.raised_at at time zone 'Europe/London')::date            as raised_date,
    date_trunc('month', w.raised_at at time zone 'Europe/London')::date as raised_month,
    w.status,
    w.contractor,
    w.labour_cost_gbp,
    w.material_cost_gbp,
    w.total_cost_gbp,
    w.hours_to_complete,
    s.target_hours,
    case when w.completed_at is not null then w.hours_to_complete <= s.target_hours end as is_sla_met
from {{ ref('stg_work_orders') }} w
join {{ ref('dim_building') }} b using (building_id)
left join {{ ref('priority_slas') }} s using (priority)
