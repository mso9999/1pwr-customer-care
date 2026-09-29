"""Tell a 1Meter failure apart from a site-wide outage.

A dark 1Meter fleet is not, by itself, evidence the village is down. SparkMeter
readings and the site PCS (generation inverter telemetry in ``inverter_readings``,
polled about once a minute — Sinosoar and the other gensite adapters) are
independent. When either of those is still reporting and no 1Meter is, the site
is up and the 1Meters need attention. When every source the site has is silent,
that matches a site-wide outage.

Windows follow how each source actually arrives:
- 1Meter: a healthy meter publishes about every 2 minutes; stored cadence is
  about 15 minutes. Live means a reading in the last 20 minutes.
- SparkMeter: ThunderCloud live import is a 15-minute cycle. Live means a
  reading in the last 45 minutes (one missed cycle still counts).
- Site PCS: the gensite poller runs every minute. Live means a reading in the
  last 10 minutes.

A source that is merely late, inside the gap between "live" and "has been down
for a while", does not decide either flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

ONE_METER_LIVE = timedelta(minutes=20)
SPARK_LIVE = timedelta(minutes=45)
PCS_LIVE = timedelta(minutes=10)
SPARK_OUTAGE = timedelta(hours=2)
PCS_OUTAGE = timedelta(minutes=30)
LOOKBACK = timedelta(hours=6)

SPARK_SOURCES = ("thundercloud", "koios")


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _age(ts: Optional[datetime], now: datetime) -> Optional[timedelta]:
    seen = _aware(ts)
    if seen is None:
        return None
    return now - seen


def _site(value: Any) -> str:
    return str(value or "").strip().upper()


def _iso(ts: Optional[datetime]) -> Optional[str]:
    seen = _aware(ts)
    if seen is None:
        return None
    return seen.isoformat()


def classify_site(
    *,
    site: str,
    onemeter_known: int,
    onemeter_live: int,
    onemeter_last_seen: Optional[datetime],
    has_spark: bool,
    spark_last_seen: Optional[datetime],
    has_pcs: bool,
    pcs_last_seen: Optional[datetime],
    now: datetime,
) -> Dict[str, Any]:
    """One site. ``condition`` is ok, meter_fault, site_outage, uncertain, or none."""
    spark_age = _age(spark_last_seen, now)
    pcs_age = _age(pcs_last_seen, now)
    # A couple of minutes of clock skew still counts as live. A reading that is
    # hours in the future does not.
    skew = timedelta(minutes=2)
    spark_live = bool(has_spark and spark_age is not None and -skew <= spark_age <= SPARK_LIVE)
    pcs_live = bool(has_pcs and pcs_age is not None and -skew <= pcs_age <= PCS_LIVE)

    if onemeter_known <= 0:
        condition = "none"
    elif onemeter_live > 0:
        condition = "ok"
    elif spark_live or pcs_live:
        condition = "meter_fault"
    else:
        silent: List[bool] = []
        if has_spark:
            silent.append(spark_age is None or spark_age > SPARK_OUTAGE)
        if has_pcs:
            silent.append(pcs_age is None or pcs_age > PCS_OUTAGE)
        condition = "site_outage" if silent and all(silent) else "uncertain"

    return {
        "site": site,
        "condition": condition,
        "onemeter_known": onemeter_known,
        "onemeter_live": onemeter_live,
        "onemeter_last_seen": _iso(onemeter_last_seen),
        "has_spark": has_spark,
        "spark_live": spark_live,
        "spark_last_seen": _iso(spark_last_seen),
        "has_pcs": has_pcs,
        "pcs_live": pcs_live,
        "pcs_last_seen": _iso(pcs_last_seen),
    }


def build_report(
    onemeters: Sequence[Sequence[Any]],
    spark_readings: Sequence[Sequence[Any]],
    spark_sites: Iterable[str],
    pcs_readings: Sequence[Sequence[Any]],
    pcs_sites: Iterable[str],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Assemble per-site rows. Only meter_fault and site_outage are returned."""
    now = now or datetime.now(timezone.utc)
    spark_last: Dict[str, datetime] = {}
    for site, ts in spark_readings:
        code = _site(site)
        if code and ts is not None:
            spark_last[code] = ts
    pcs_last: Dict[str, datetime] = {}
    for site, ts in pcs_readings:
        code = _site(site)
        if code and ts is not None:
            pcs_last[code] = ts
    spark_present: Set[str] = {_site(s) for s in spark_sites if _site(s)} | set(spark_last)
    pcs_present: Set[str] = {_site(s) for s in pcs_sites if _site(s)} | set(pcs_last)

    flagged: List[Dict[str, Any]] = []
    for site, known, live, last_seen in onemeters:
        code = _site(site)
        if not code:
            continue
        row = classify_site(
            site=code,
            onemeter_known=int(known or 0),
            onemeter_live=int(live or 0),
            onemeter_last_seen=last_seen,
            has_spark=code in spark_present,
            spark_last_seen=spark_last.get(code),
            has_pcs=code in pcs_present,
            pcs_last_seen=pcs_last.get(code),
            now=now,
        )
        if row["condition"] in ("meter_fault", "site_outage"):
            flagged.append(row)
    flagged.sort(key=lambda r: (r["condition"] != "meter_fault", r["site"]))
    return flagged


def load_site_reporting(conn, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Read 1Meter, SparkMeter, and site-PCS freshness for this country database."""
    now = now or datetime.now(timezone.utc)
    since = now - LOOKBACK
    cur = conn.cursor()
    cur.execute(
        """
        SELECT UPPER(TRIM(m.community)) AS site,
               COUNT(*) AS known,
               COUNT(*) FILTER (
                   WHERE s.last_seen_at >= %s
               ) AS live,
               MAX(s.last_seen_at) AS last_seen
        FROM meters m
        LEFT JOIN prototype_meter_state s
          ON ltrim(s.meter_id, '0') = ltrim(m.meter_id, '0')
        WHERE m.platform = 'prototype'
          AND m.community IS NOT NULL
          AND TRIM(m.community) <> ''
          AND (m.status IS NULL OR m.status <> 'decommissioned')
        GROUP BY 1
        """,
        (now - ONE_METER_LIVE,),
    )
    onemeters = cur.fetchall()

    cur.execute(
        """
        SELECT UPPER(TRIM(community)) AS site, MAX(reading_time) AS last_seen
        FROM meter_readings
        WHERE source IN ('thundercloud', 'koios')
          AND reading_time >= %s
          AND community IS NOT NULL
        GROUP BY 1
        """,
        (since,),
    )
    spark_readings = cur.fetchall()

    cur.execute(
        """
        SELECT DISTINCT UPPER(TRIM(community)) AS site
        FROM meters
        WHERE platform = 'sparkmeter'
          AND community IS NOT NULL
          AND TRIM(community) <> ''
          AND (status IS NULL OR status <> 'decommissioned')
        """
    )
    spark_sites = [row[0] for row in cur.fetchall()]

    cur.execute(
        """
        SELECT UPPER(site_code) AS site, MAX(ts_utc) AS last_seen
        FROM inverter_readings
        WHERE ts_utc >= %s
        GROUP BY 1
        """,
        (since,),
    )
    pcs_readings = cur.fetchall()

    cur.execute(
        """
        SELECT DISTINCT UPPER(site_code) AS site
        FROM site_equipment
        WHERE decommissioned_at IS NULL
        """
    )
    pcs_sites = [row[0] for row in cur.fetchall()]
    return build_report(onemeters, spark_readings, spark_sites, pcs_readings, pcs_sites, now)
