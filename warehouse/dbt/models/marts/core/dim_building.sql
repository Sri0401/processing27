select
    building_id,
    site_id,
    building_name,
    space_usage,
    sub_space_usage,
    floor_area_m2,
    number_of_floors,
    year_built_or_imputed                                       as year_built,
    is_year_built_imputed,
    {{ var('asset_reference_year') }} - year_built_or_imputed   as building_age_years,
    case
        when year_built_or_imputed < 1945 then 'Pre-1945'
        when year_built_or_imputed < 1975 then '1945-1974'
        when year_built_or_imputed < 2004 then '1975-2003'
        else '2004 onwards'
    end                                                         as age_band,
    case
        when floor_area_m2 < 1000 then 'Small (<1,000 m2)'
        when floor_area_m2 < 5000 then 'Medium (1,000-5,000 m2)'
        else 'Large (5,000+ m2)'
    end                                                         as size_band,
    has_electricity_meter,
    has_gas_meter,
    lat,
    lng
from {{ ref('stg_buildings') }}
