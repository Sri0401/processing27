select
    site_id,
    building_id,
    meter,
    ts           as reading_ts,
    value        as kwh,
    source       as load_source,
    ingested_at
from {{ source('raw', 'meter_reading') }}
