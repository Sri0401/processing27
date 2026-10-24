select
    survey_id,
    asset_id,
    survey_date,
    condition_grade::text as condition_grade,
    surveyor
from {{ source('raw', 'condition_survey') }}
