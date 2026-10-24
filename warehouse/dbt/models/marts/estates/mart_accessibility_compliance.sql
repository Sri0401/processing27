{#-
  Grain: one row per building - latest audit outcome.
  compliance_pct = (passes + 0.5 x partials) / applicable checks.
  RAG: Red if any high-severity check fails or compliance < 60%; Amber < 90%; else Green.
-#}
with checks as (
    select * from {{ ref('stg_accessibility_checks') }}
)

select
    c.building_id,
    b.building_name,
    b.space_usage,
    b.age_band,
    max(c.audit_date)                                                           as audit_date,
    count(*) filter (where c.is_applicable)                                     as applicable_checks,
    count(*) filter (where c.result = 'pass')                                   as passed,
    count(*) filter (where c.result = 'partial')                                as partial,
    count(*) filter (where c.result = 'fail')                                   as failed,
    count(*) filter (where c.result = 'fail' and c.severity = 'high')           as high_severity_fails,
    avg(c.compliance_score) filter (where c.is_applicable)                      as compliance_pct,
    sum(c.remediation_cost_gbp)                                                 as remediation_cost_gbp,
    case
        when count(*) filter (where c.result = 'fail' and c.severity = 'high') > 0
          or avg(c.compliance_score) filter (where c.is_applicable) < 0.6 then 'Red'
        when avg(c.compliance_score) filter (where c.is_applicable) < 0.9 then 'Amber'
        else 'Green'
    end                                                                         as rag_status
from checks c
join {{ ref('dim_building') }} b using (building_id)
group by c.building_id, b.building_name, b.space_usage, b.age_band
