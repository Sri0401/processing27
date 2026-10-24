select
    audit_id,
    building_id,
    audit_date,
    check_code,
    check_name,
    result,
    severity,
    remediation_cost_gbp,
    result <> 'n/a' as is_applicable,
    case result when 'pass' then 1.0 when 'partial' then 0.5 when 'fail' then 0.0 end as compliance_score
from {{ source('raw', 'accessibility_audit') }}
