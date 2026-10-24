{#-
  "How much would we save if each building ran out-of-hours like a typical peer?"

  For each building/meter, the out-of-hours share of consumption is compared with the
  median share of buildings with the same space use. If it is higher, we keep occupied
  consumption as-is and reduce unoccupied consumption until the share matches the peer
  median:     target_unoccupied = peer_share / (1 - peer_share) * occupied_kwh
  Savings are valued at the building's own average unit cost and carbon intensity.
  24/7 uses (residences, healthcare) have no unoccupied hours and drop out naturally.
-#}
with kpis as (
    select * from {{ ref('mart_building_energy_kpis') }}
    where data_coverage >= 0.9 and kwh > 0 and unoccupied_kwh > 0 and occupied_kwh > 0
),

peer as (
    select
        space_usage,
        meter,
        count(*)                                                        as peer_count,
        percentile_cont(0.5) within group (order by unoccupied_share)   as peer_median_unoccupied_share
    from kpis
    group by 1, 2
),

calc as (
    select
        k.building_id,
        k.building_name,
        k.space_usage,
        k.meter,
        k.kpi_year,
        k.kwh,
        k.occupied_kwh,
        k.unoccupied_kwh,
        k.unoccupied_share,
        p.peer_count,
        p.peer_median_unoccupied_share,
        k.cost_gbp / k.kwh                                              as avg_gbp_per_kwh,
        k.tco2e / k.kwh                                                 as avg_tco2e_per_kwh,
        greatest(
            k.unoccupied_kwh
            - p.peer_median_unoccupied_share / (1 - p.peer_median_unoccupied_share) * k.occupied_kwh,
            0
        )                                                               as savings_kwh
    from kpis k
    join peer p using (space_usage, meter)
    where p.peer_count >= 3          -- not enough peers -> no benchmark
)

select
    *,
    savings_kwh * avg_gbp_per_kwh                                       as savings_gbp,
    savings_kwh * avg_tco2e_per_kwh                                     as savings_tco2e,
    savings_kwh / nullif(kwh, 0)                                        as savings_share_of_total,
    rank() over (order by savings_kwh * avg_gbp_per_kwh desc)           as opportunity_rank
from calc
