select
    work_order_id,
    asset_id,
    building_id,
    raised_at,
    completed_at,
    work_type,
    priority,
    category                                        as asset_class,
    description,
    coalesce(labour_cost_gbp, 0)                    as labour_cost_gbp,
    coalesce(material_cost_gbp, 0)                  as material_cost_gbp,
    coalesce(labour_cost_gbp, 0) + coalesce(material_cost_gbp, 0) as total_cost_gbp,
    contractor,
    status,
    extract(epoch from completed_at - raised_at) / 3600.0 as hours_to_complete
from {{ source('raw', 'work_order') }}
