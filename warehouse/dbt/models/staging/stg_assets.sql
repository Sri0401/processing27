select
    asset_id,
    building_id,
    asset_class,
    description          as asset_description,
    install_year,
    expected_life_years,
    install_year + expected_life_years as end_of_life_year,
    replacement_cost_gbp,
    criticality
from {{ source('raw', 'asset_register') }}
