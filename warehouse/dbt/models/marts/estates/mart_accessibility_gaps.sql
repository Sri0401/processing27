-- Grain: one row per failed / partially met accessibility check - the remediation worklist.
select
    c.building_id,
    b.building_name,
    b.space_usage,
    c.audit_date,
    c.check_code,
    c.check_name,
    c.result,
    c.severity,
    c.remediation_cost_gbp,
    case c.severity when 'high' then 1 when 'medium' then 2 else 3 end as severity_rank
from {{ ref('stg_accessibility_checks') }} c
join {{ ref('dim_building') }} b using (building_id)
where c.result in ('fail', 'partial')
