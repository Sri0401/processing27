-- A building cannot save more out-of-hours energy than it uses out of hours.
select building_id, meter, savings_kwh, unoccupied_kwh
from {{ ref('mart_out_of_hours_opportunity') }}
where savings_kwh > unoccupied_kwh + 0.01
