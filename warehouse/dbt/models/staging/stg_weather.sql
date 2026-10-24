select
    site_id,
    ts                 as weather_ts,
    air_temperature    as air_temperature_c,
    dew_temperature    as dew_temperature_c,
    wind_speed         as wind_speed_ms,
    cloud_coverage,
    precip_depth_1hr   as precip_mm
from {{ source('raw', 'weather_hourly') }}
