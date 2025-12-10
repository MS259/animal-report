from datetime import datetime
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

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


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Animal Report API running"}


@app.post("/report")
def create_report(report: Report):
    """
    Store a new report:
    - write to reports.log (legacy)
    - insert into SQLite database
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
        session.refresh(record)

    return {"status": "ok", "id": record.id}


@app.get("/reports")
def list_reports(limit: int = 100):
    """
    Return the most recent reports from the database.
    This is what the dashboard will call.
    """
    with SessionLocal() as session:
        records = (
            session.query(ReportRecord)
            .order_by(ReportRecord.timestamp.desc())
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
        const counter = document.getElementById('counter');
        counter.textContent = data.length + ' reports';

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
        const counter = document.getElementById('counter');
        counter.textContent = 'Error loading';
      }
    }

    loadReports();
  </script>

</body>
</html>
"""
