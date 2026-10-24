{#-
  Grain: one row per asset - latest condition, deterioration, backlog and a risk-based priority.

  backlog_cost_gbp  replacement cost x backlog factor for the grade (seed: condition_grades)
  risk_score        grade score (1-4) x criticality weight (critical=3 .. low=1), plus 1 if the
                    asset needed 3+ reactive repairs in the reference year. Range 1-13.
-#}
{%- set ref_year = var('asset_reference_year') -%}

with surveys as (
    select
        *,
        row_number() over (partition by asset_id order by survey_date desc) as recency,
        lead(condition_grade) over (partition by asset_id order by survey_date desc) as previous_grade
    from {{ ref('stg_condition_surveys') }}
),

latest as (
    select * from surveys where recency = 1
),

reactive as (
    select
        asset_id,
        count(*)                as reactive_jobs,
        sum(total_cost_gbp)     as reactive_cost_gbp
    from {{ ref('fct_work_orders') }}
    where work_type = 'reactive' and extract(year from raised_date) = {{ ref_year }}
    group by 1
),

scored as (
    select
        a.asset_id,
        a.building_id,
        b.building_name,
        b.space_usage,
        a.asset_class,
        a.asset_description,
        a.install_year,
        {{ ref_year }} - a.install_year                             as age_years,
        a.expected_life_years,
        a.end_of_life_year - {{ ref_year }}                         as remaining_life_years,
        a.criticality,
        a.replacement_cost_gbp,
        l.survey_date                                               as last_survey_date,
        l.condition_grade,
        g.label                                                     as condition_label,
        l.previous_grade,
        l.previous_grade is not null and l.condition_grade > l.previous_grade as has_deteriorated,
        a.replacement_cost_gbp * g.backlog_factor                   as backlog_cost_gbp,
        coalesce(r.reactive_jobs, 0)                                as reactive_jobs_ref_year,
        coalesce(r.reactive_cost_gbp, 0)                            as reactive_cost_gbp_ref_year,
        g.grade_score * (4 - a.criticality)
            + case when coalesce(r.reactive_jobs, 0) >= 3 then 1 else 0 end as risk_score
    from {{ ref('stg_assets') }} a
    join {{ ref('dim_building') }} b using (building_id)
    left join latest l using (asset_id)
    left join {{ ref('condition_grades') }} g on g.condition_grade = l.condition_grade
    left join reactive r using (asset_id)
)

select
    *,
    case
        when risk_score >= 9 then '1 - Replace now'
        when risk_score >= 6 then '2 - Plan within 2 years'
        when risk_score >= 4 then '3 - Plan within 5 years'
        else '4 - Monitor'
    end                                                             as investment_priority
from scored
