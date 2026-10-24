-- Dead-lettered telemetry by day and reason, so contract breaches are visible to the owner.
select
    received_at::date                       as received_date,
    split_part(reason, ':', 1)              as reason,
    payload ->> 'building_id'               as building_id,
    count(*)                                as rejects
from {{ source('raw', 'telemetry_reject') }}
group by 1, 2, 3
