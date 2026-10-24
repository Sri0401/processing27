{#-
  Data-quality flags for every hourly reading.

  is_spike  reading exceeds the trailing 7-day mean by `spike_sigma` standard deviations
            (and is at least double the mean, so low-variance meters are not over-flagged)
  is_stuck  the reading belongs to a run of >= `stuck_hours` consecutive identical non-zero
            values (gaps-and-islands: a value change or a missing hour starts a new run).
            Calibrated on BDG2, whose "cleaned" files contain 72h+ constant gap-fill runs.

  Incremental: each run reprocesses the last `late_data_lookback_days` days so late or
  corrected telemetry is picked up, reading 8 extra days of history purely as window context.
  RANGE windows (not ROWS) keep the statistics correct across telemetry gaps.
-#}
{{
  config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['building_id', 'meter', 'reading_ts'],
    indexes=[
      {'columns': ['building_id', 'meter', 'reading_ts'], 'unique': True},
      {'columns': ['reading_ts']},
    ],
  )
}}

{%- set lookback = var('late_data_lookback_days') -%}
{%- set stuck_hours = var('stuck_hours') -%}

with readings as (
    select * from {{ ref('stg_meter_readings') }}
    {% if is_incremental() %}
    where reading_ts >= (select max(reading_ts) from {{ this }})
                        - interval '{{ lookback }} days' - interval '8 days'
    {% endif %}
),

run_starts as (
    select
        *,
        case
            when lag(kwh) over meter_series = kwh
             and reading_ts - lag(reading_ts) over meter_series = interval '1 hour' then 0
            else 1
        end as is_run_start
    from readings
    window meter_series as (partition by building_id, meter order by reading_ts)
),

runs as (
    select
        *,
        sum(is_run_start) over (partition by building_id, meter order by reading_ts) as run_id
    from run_starts
),

windowed as (
    select
        *,
        avg(kwh)         over trailing_week as trailing_mean_kwh,
        stddev_samp(kwh) over trailing_week as trailing_std_kwh,
        count(*)         over (partition by building_id, meter, run_id) as run_length_hours
    from runs
    window
        trailing_week as (
            partition by building_id, meter order by reading_ts
            range between interval '168 hours' preceding and interval '1 hour' preceding
        )
)

select
    site_id,
    building_id,
    meter,
    reading_ts,
    kwh,
    load_source,
    ingested_at,
    trailing_mean_kwh,
    trailing_std_kwh,
    coalesce(
        kwh > trailing_mean_kwh + {{ var('spike_sigma') }} * greatest(trailing_std_kwh, 0.5)
        and kwh > 2 * trailing_mean_kwh + 1,
        false
    )                                                                   as is_spike,
    run_length_hours,
    (run_length_hours >= {{ stuck_hours }} and kwh > 0)                 as is_stuck
from windowed
{% if is_incremental() %}
where reading_ts >= (select max(reading_ts) from {{ this }}) - interval '{{ lookback }} days'
{% endif %}
