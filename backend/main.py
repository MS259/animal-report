from datetime import datetime
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional, List
from fastapi import Query
from sqlalchemy import func
from fastapi import BackgroundTasks






from db import SessionLocal, ReportRecord, init_db

# Initialise database (create tables if they don't exist)
init_db()

app = FastAPI(title="Animal Report API")

# Allow web app (and later phone app) to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # later we can restrict this
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

def process_report(report: Report) -> None:
    """
    Background task: log report and store in DB.
    """
    line = (
        f"{report.timestamp.isoformat()} | "
        f"{report.type} | "
        f"{report.latitude},{report.longitude}\n"
    )

    # Log to console
    print("NEW REPORT:", line.strip())

    # Legacy: still write to file for now
    with open("reports.log", "a", encoding="utf-8") as f:
        f.write(line)

    # Write to DB
    with SessionLocal() as session:
        record = ReportRecord(
            type=report.type,
            latitude=report.latitude,
            longitude=report.longitude,
            timestamp=report.timestamp,
        )
        session.add(record)
        session.commit()


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Animal Report API running"}


@app.post("/report")
def create_report(report: Report, background_tasks: BackgroundTasks):
    """
    Store a new report:

    - enqueue background task to log to file
    - enqueue background task to insert into the database

    Returns immediately without waiting for the DB write.
    """
    background_tasks.add_task(process_report, report)

    return {
        "status": "queued",
        "message": "Report accepted for processing"
    }






@app.get("/reports")
def list_reports(
    limit: int = 100,
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    """
    Return the most recent reports from the database.
    This is what the dashboard will call.

    Optional filters:
    - ?from=YYYY-MM-DD  (inclusive)
    - ?to=YYYY-MM-DD    (inclusive)
    - ?limit=100
    """
    with SessionLocal() as session:
        query = session.query(ReportRecord)

        # from >=
        if from_date is not None:
            start_dt = datetime.combine(from_date, datetime.min.time())
            query = query.filter(ReportRecord.timestamp >= start_dt)

        # to <=
        if to_date is not None:
            end_dt = datetime.combine(to_date, datetime.max.time())
            query = query.filter(ReportRecord.timestamp <= end_dt)

        records = (
            query.order_by(ReportRecord.timestamp.desc())
            .limit(limit)
            .all()
        )

    return [
        {
            "id": r.id,
            "type": r.type,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in records
    ]

@app.get("/stats", response_model=StatsResponse)
def get_stats(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    """
    Basic statistics over all reports and over an optional time window.

    Query params:
    - ?from=YYYY-MM-DD  (inclusive)
    - ?to=YYYY-MM-DD    (inclusive)
    If no from/to given, window = all data.
    """
    with SessionLocal() as session:
        # total in DB (no filters)
        total = session.query(func.count(ReportRecord.id)).scalar() or 0

        # build filtered query for the window
        window_query = session.query(ReportRecord)

        if from_date is not None:
            start_dt = datetime.combine(from_date, datetime.min.time())
            window_query = window_query.filter(ReportRecord.timestamp >= start_dt)

        if to_date is not None:
            end_dt = datetime.combine(to_date, datetime.max.time())
            window_query = window_query.filter(ReportRecord.timestamp <= end_dt)

        # count in window
        window_total = window_query.count()

        # breakdown by type in this window
        by_type_rows = (
            window_query
            .with_entities(ReportRecord.type, func.count(ReportRecord.id))
            .group_by(ReportRecord.type)
            .all()
        )

        by_type = {t: c for (t, c) in by_type_rows if t is not None}

    return StatsResponse(
        total=total,
        window_total=window_total,
        by_type=by_type,
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """
    Simple HTML dashboard with a Leaflet map
    that visualises all reports from /reports.
    """
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Animal Reports Map</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
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
    }
  </style>
</head>
<body>
  <div class="header">
    <div>
      <div class="header-title">Animal Reports</div>
      <div class="header-sub">Live map of reported animals</div>
    </div>
    <div class="pill" id="counter">Loading…</div>
  </div>
  <div id="map"></div>
  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>
    <script>
    const map = L.map('map').setView([54.5, -2.5], 6); // roughly UK

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    async function loadReports() {
      try {
        const res = await fetch('/reports?limit=500');
        const data = await res.json();

        // Group reports by approximate lat/lon
        const groups = {};

        data.forEach(r => {
          if (r.latitude == null || r.longitude == null) return;
          const key = r.latitude.toFixed(4) + ',' + r.longitude.toFixed(4);
          if (!groups[key]) {
            groups[key] = {
              lat: r.latitude,
              lon: r.longitude,
              total: 0,
              dead: 0,
              injured: 0,
            };
          }
          groups[key].total += 1;
          if (r.type === 'dead') groups[key].dead += 1;
          if (r.type === 'injured') groups[key].injured += 1;
        });

        // Create one marker per location, with counts in the popup
        Object.values(groups).forEach(g => {
          const marker = L.marker([g.lat, g.lon]).addTo(map);
          marker.bindPopup(
            `<strong>${g.total} report(s)</strong><br/>
             dead: ${g.dead}, injured: ${g.injured}<br/>
             ${g.lat.toFixed(4)}, ${g.lon.toFixed(4)}`
          );
        });
      } catch (e) {
        console.error(e);
      }
    }

    async function loadStats() {
      try {
        const res = await fetch('/stats');
        const data = await res.json();
        const counter = document.getElementById('counter');

        const dead = data.by_type.dead || 0;
        const injured = data.by_type.injured || 0;

        // Example: "14 total · dead: 10 · injured: 4"
        counter.textContent = `${data.total} total · dead: ${dead} · injured: ${injured}`;
      } catch (e) {
        console.error(e);
        const counter = document.getElementById('counter');
        counter.textContent = 'Stats error';
      }
    }

    // Load both map markers and stats
    loadReports();
    loadStats();
  </script>

</body>
</html>
"""
