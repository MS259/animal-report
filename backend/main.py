from datetime import datetime, date, timedelta
from typing import Literal, Optional

import hashlib
import math

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi import Query
from sqlalchemy import func

from db import SessionLocal, ReportRecord, IncidentRecord, init_db

# Initialise database (create tables if they don't exist + schema patch)
init_db()

app = FastAPI(title="Animal Report API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Report(BaseModel):
    type: Literal["dead", "injured"]
    latitude: float
    longitude: float
    timestamp: datetime


class StatsResponse(BaseModel):
    total: int
    window_total: int
    by_type: dict


# -------------------------
# Helpers
# -------------------------
def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def bucket(v: float, size: float = 0.001) -> int:
    return int(math.floor(v / size))


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# -------------------------
# Incident + anti-spam settings (v1)
# -------------------------
RADIUS_M = 100
WINDOW_MIN = 15
CONFIRM_REPORTS = 5
#CONFIRM_UNIQUE_DEVICES = 3

THROTTLE_MAX_IN_2MIN = 20   # was 3
DUPLICATE_MIN = 0.25        # 15 seconds (was 2)
DUPLICATE_M = 25            # was 50


def should_reject(session, device_hash: str, rep_type: str, lat: float, lon: float, now_dt: datetime):
    # throttle: max N in last 2 minutes
    since = now_dt - timedelta(minutes=2)
    n = session.query(ReportRecord).filter(
        ReportRecord.device_hash == device_hash,
        ReportRecord.received_at >= since
    ).count()
    if n >= THROTTLE_MAX_IN_2MIN:
        return True, "throttle_2min"

    # duplicate near-identical: same type, within 2 min and 50m
    last = session.query(ReportRecord).filter(
        ReportRecord.device_hash == device_hash,
        ReportRecord.type == rep_type,
        ReportRecord.accepted == True,
        ReportRecord.received_at >= (now_dt - timedelta(minutes=DUPLICATE_MIN)),
    ).order_by(ReportRecord.received_at.desc()).first()

    if last:
        d = haversine_m(last.latitude, last.longitude, lat, lon)
        if d <= DUPLICATE_M:
            return True, "duplicate_nearby"

    return False, None


def find_candidate_incident(session, rep_type: str, lat: float, lon: float, lat_b: int, lon_b: int, now_dt: datetime):
    window_start = now_dt - timedelta(minutes=WINDOW_MIN)

    candidates = session.query(IncidentRecord).filter(
        IncidentRecord.status.in_(["pending", "confirmed"]),
        IncidentRecord.type == rep_type,
        IncidentRecord.last_report_at >= window_start,
        IncidentRecord.lat_bucket.between(lat_b - 1, lat_b + 1),
        IncidentRecord.lon_bucket.between(lon_b - 1, lon_b + 1),
    ).order_by(IncidentRecord.last_report_at.desc()).limit(25).all()

    best = None
    best_dist = None
    for inc in candidates:
        d = haversine_m(inc.centroid_lat, inc.centroid_lon, lat, lon)
        if d <= RADIUS_M and (best is None or d < best_dist):
            best = inc
            best_dist = d

    return best


def recalc_incident(session, incident_id: int):
    reps = session.query(ReportRecord).filter(
        ReportRecord.incident_id == incident_id,
        ReportRecord.accepted == True
    ).all()

    if not reps:
        return

    count = len(reps)
    unique_devices = len({r.device_hash for r in reps if r.device_hash})

    centroid_lat = sum(r.latitude for r in reps) / count
    centroid_lon = sum(r.longitude for r in reps) / count
    first_ts = min(r.timestamp for r in reps if r.timestamp)
    last_ts = max(r.timestamp for r in reps if r.timestamp)

    inc = session.get(IncidentRecord, incident_id)
    inc.report_count = count
    inc.unique_device_count = unique_devices
    inc.centroid_lat = centroid_lat
    inc.centroid_lon = centroid_lon
    inc.first_report_at = first_ts
    inc.last_report_at = last_ts
    inc.lat_bucket = bucket(centroid_lat)
    inc.lon_bucket = bucket(centroid_lon)

    if count >= CONFIRM_REPORTS and (unique_devices >= CONFIRM_UNIQUE_DEVICES or unique_devices == 0):
        inc.status = "confirmed"
    else:
        inc.status = "pending"


# -------------------------
# Routes
# -------------------------
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Animal Report API running"}


@app.post("/report")
def create_report(report: Report, request: Request):
    now_dt = datetime.utcnow()

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    device_hash = sha256(f"{ip}|{ua}") if (ip or ua) else None
    ip_hash = sha256(ip) if ip else None
    ua_hash = sha256(ua) if ua else None

    with SessionLocal() as session:
        reject, reason = (False, None)
        if device_hash:
            reject, reason = should_reject(session, device_hash, report.type, report.latitude, report.longitude, now_dt)

        record = ReportRecord(
            type=report.type,
            latitude=report.latitude,
            longitude=report.longitude,
            timestamp=report.timestamp,
            received_at=now_dt,
            device_hash=device_hash,
            ip_hash=ip_hash,
            ua_hash=ua_hash,
            accepted=(not reject),
            reject_reason=reason,
        )
        session.add(record)
        session.flush()  # gives record.id

        incident_id = None
        incident_status = None

        if not reject:
            lat_b = bucket(report.latitude)
            lon_b = bucket(report.longitude)

            inc = find_candidate_incident(session, report.type, report.latitude, report.longitude, lat_b, lon_b, now_dt)
            if inc is None:
                inc = IncidentRecord(
                    status="pending",
                    type=report.type,
                    centroid_lat=report.latitude,
                    centroid_lon=report.longitude,
                    first_report_at=report.timestamp,
                    last_report_at=report.timestamp,
                    report_count=0,
                    unique_device_count=0,
                    lat_bucket=lat_b,
                    lon_bucket=lon_b,
                )
                session.add(inc)
                session.flush()

            record.incident_id = inc.id
            recalc_incident(session, inc.id)

            incident_id = inc.id
            incident_status = inc.status

        session.commit()
        session.refresh(record)

    # keep old response fields + add new (UI can ignore)
    return {
        "status": "ok",
        "id": record.id,
        "accepted": (not reject),
        "reject_reason": reason,
        "incident_id": incident_id,
        "incident_status": incident_status,
    }


@app.get("/reports")
def list_reports(
    limit: int = 100,
    accepted_only: bool = True,
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    with SessionLocal() as session:
        query = session.query(ReportRecord)

        if accepted_only:
            query = query.filter(ReportRecord.accepted == True)

        if from_date is not None:
            start_dt = datetime.combine(from_date, datetime.min.time())
            query = query.filter(ReportRecord.timestamp >= start_dt)

        if to_date is not None:
            end_dt = datetime.combine(to_date, datetime.max.time())
            query = query.filter(ReportRecord.timestamp <= end_dt)

        records = query.order_by(ReportRecord.timestamp.desc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "type": r.type,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "accepted": r.accepted,
            "incident_id": r.incident_id,
        }
        for r in records
    ]


@app.get("/incidents")
def list_incidents(status: str = "confirmed", hours: int = 24, limit: int = 500):
    since = datetime.utcnow() - timedelta(hours=hours)
    with SessionLocal() as session:
        q = session.query(IncidentRecord).filter(
            IncidentRecord.last_report_at >= since
        )

        if status != "all":
            q = q.filter(IncidentRecord.status == status)

        q = q.order_by(IncidentRecord.last_report_at.desc()).limit(limit)
        items = q.all()

    return [
        {
            "id": i.id,
            "status": i.status,
            "type": i.type,
            "latitude": i.centroid_lat,
            "longitude": i.centroid_lon,
            "report_count": i.report_count,
            "unique_device_count": i.unique_device_count,
            "first_report_at": i.first_report_at.isoformat() if i.first_report_at else None,
            "last_report_at": i.last_report_at.isoformat() if i.last_report_at else None,
        }
        for i in items
    ]


@app.get("/stats", response_model=StatsResponse)
def get_stats(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    with SessionLocal() as session:
        total = session.query(func.count(ReportRecord.id)).filter(ReportRecord.accepted == True).scalar() or 0

        window_query = session.query(ReportRecord).filter(ReportRecord.accepted == True)

        if from_date is not None:
            start_dt = datetime.combine(from_date, datetime.min.time())
            window_query = window_query.filter(ReportRecord.timestamp >= start_dt)

        if to_date is not None:
            end_dt = datetime.combine(to_date, datetime.max.time())
            window_query = window_query.filter(ReportRecord.timestamp <= end_dt)

        window_total = window_query.count()

        by_type_rows = (
            window_query
            .with_entities(ReportRecord.type, func.count(ReportRecord.id))
            .group_by(ReportRecord.type)
            .all()
        )

        by_type = {t: c for (t, c) in by_type_rows if t is not None}

    return StatsResponse(total=total, window_total=window_total, by_type=by_type)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """
    Simple HTML dashboard with a Leaflet map
    that visualises CONFIRMED incidents from /incidents.
    """
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Animal Incidents Map</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />

  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  />

  <!-- MarkerCluster plugin -->
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"
  />
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"
  />

  <style>
    html, body { height: 100%; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    #map { height: calc(100vh - 60px); width: 100%; }

    .header {
      height: 60px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 16px;
      background: #1f8ff0;
      color: white;
      box-sizing: border-box;
    }
    .header-title {
      font-size: 18px;
      font-weight: 600;
    }
    .header-sub {
      font-size: 12px;
      opacity: 0.85;
    }
    .pill {
      background: rgba(255, 255, 255, 0.2);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 11px;
      white-space: nowrap;
    }

    /* Simple coloured dot markers (divIcon) */
    .incident-dot {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.95);
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }
    .dot-dead { background: #e11d48; }     /* red */
    .dot-injured { background: #f59e0b; }  /* amber */
  </style>
</head>

<body>
  <div class="header">
    <div>
      <div class="header-title">Confirmed Animal Incidents</div>
      <div class="header-sub">Auto-grouped from public reports</div>
    </div>
    <div class="pill" id="counter">Loading…</div>
  </div>

  <div id="map"></div>

  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>

  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

  <script>
    // 1) Create map
    const map = L.map('map').setView([54.5, -2.5], 6); // UK default

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    // 2) Cluster layer
    const cluster = L.markerClusterGroup();
    map.addLayer(cluster);

    let didAutoFit = false;

    function iconFor(type) {
      const cls = (type === 'dead') ? 'dot-dead' : 'dot-injured';
      return L.divIcon({
        className: '',
        html: `<div class="incident-dot ${cls}"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7]
      });
    }

    // 3) Load confirmed incidents
    async function loadIncidents() {
      try {
        const res = await fetch('/incidents?status=all&hours=168&limit=1000', { cache: 'no-store' });
        const data = await res.json();

        cluster.clearLayers();

        const points = [];

        data.forEach(i => {
          if (i.latitude == null || i.longitude == null) return;

          points.push([i.latitude, i.longitude]);

          const marker = L.marker([i.latitude, i.longitude], { icon: iconFor(i.type) });

          const emoji = i.type === 'dead' ? '☠️' : '🚑';
          const reports = i.report_count ?? 0;
          const devices = i.unique_device_count ?? 0;

          marker.bindPopup(
            `<strong>${emoji} Incident #${i.id}</strong><br/>
             type: <b>${i.type}</b><br/>
             reports: <b>${reports}</b><br/>
             unique devices: <b>${devices}</b><br/>
             last: ${i.last_report_at ?? 'n/a'}<br/>
             ${Number(i.latitude).toFixed(5)}, ${Number(i.longitude).toFixed(5)}`
          );

          cluster.addLayer(marker);
        });

        document.getElementById('counter').textContent =
          `${data.length} confirmed incidents (7 days)`;

        // Auto-fit once (first successful load with data)
        if (!didAutoFit && points.length > 0) {
          const bounds = L.latLngBounds(points);
          map.fitBounds(bounds, { padding: [40, 40] });
          didAutoFit = true;
        }

      } catch (e) {
        console.error('loadIncidents error', e);
        document.getElementById('counter').textContent = 'Map error';
      }
    }

    loadIncidents();
    setInterval(loadIncidents, 5000);
  </script>

</body>
</html>
"""

