with buildings as (
    select * from {{ source('raw', 'building_metadata') }}
),

-- BDG2 lacks a build year for a few buildings; impute from the same use type, and say so
usage_median as (
    select
        primary_space_usage,
        round(percentile_cont(0.5) within group (order by year_built))::int as median_year_built
    from buildings
    group by 1
)

select
    b.building_id,
    b.site_id,
    split_part(b.building_id, '_', 3)                       as building_name,
    b.primary_space_usage                                   as space_usage,
    b.sub_primary_space_usage                               as sub_space_usage,
    b.sqm                                                   as floor_area_m2,
    b.year_built,
    coalesce(b.year_built, u.median_year_built, 1970)       as year_built_or_imputed,
    b.year_built is null                                    as is_year_built_imputed,
    b.number_of_floors,
    b.occupants,
    b.lat,
    b.lng,
    b.has_electricity_meter,
    b.has_gas_meter
from buildings b
left join usage_median u using (primary_space_usage)
