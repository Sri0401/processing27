with days as (
    select d::date as date_day
    from generate_series('2016-01-01'::date, '2017-12-31'::date, interval '1 day') d
)

select
    days.date_day,
    extract(year from date_day)::int                as year,
    extract(month from date_day)::int               as month,
    date_trunc('month', date_day)::date             as month_start,
    to_char(date_day, 'Dy')                         as day_name,
    extract(isodow from date_day) in (6, 7)         as is_weekend,
    t.academic_year,
    coalesce(t.term, 'Vacation')                    as term,
    t.term is not null                              as is_term_time
from days
left join {{ ref('academic_terms') }} t
    on days.date_day between t.start_date and t.end_date
