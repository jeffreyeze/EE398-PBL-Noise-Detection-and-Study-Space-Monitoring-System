from flask import Flask, request, jsonify, render_template_string, g
import sqlite3
import datetime
import sys

app = Flask(__name__)

# SQLite setup 
DB_PATH = 'events.db'

def get_db():
    # Use Flask's application context to store the connection (thread-safe)
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Initialize the database on first run
with app.app_context():
    db = get_db()
    db.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        decibels INTEGER NOT NULL,
        location TEXT
    )
    """)
    db.commit()

# Routes

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "alive"}), 200

@app.route('/ingest', methods=['POST'])
def ingest():
    try:
        # Get the database connection within this request context
        db = get_db()
        
        # Log minimal request info for diagnostics
        print("\n--- /ingest hit ---", file=sys.stdout, flush=True)
        raw = request.get_data()
        print("Raw body:", raw, file=sys.stdout, flush=True)

        item = request.get_json(force=True, silent=False)
        date = item.get('date', '')
        time_ = item.get('time', '')
        decibels = item.get('decibels', 0)
        location = item.get('location', '')

        # Normalize decibels to int
        try:
            decibels = int(decibels)
        except Exception:
            decibels = 0

        # Build timestamp
        ts_str = f"{date} {time_}".strip()
        if not date or not time_:
            ts_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # Persist to SQLite
        db.execute(
            "INSERT INTO events (timestamp, decibels, location) VALUES (?,?,?)",
            (ts_str, decibels, location)
        )
        db.commit()

        print("Stored event:", {
            "timestamp": ts_str,
            "decibels": decibels,
            "location": location
        }, file=sys.stdout, flush=True)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("ERROR in /ingest:", e, file=sys.stderr, flush=True)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/data', methods=['GET'])
def get_data():
    db = get_db()
    # Fetch 500 records 
    limit = request.args.get('limit', default=500, type=int) 
    rows = db.execute(
        "SELECT timestamp, decibels, location FROM events ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    # Reverse the order for the chart 
    items = [
        {"timestamp": r["timestamp"], "decibels": r["decibels"], "location": r["location"]}
        for r in reversed(rows)
    ]
    return jsonify(items)

@app.route('/', methods=['GET'])
def index():
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Noise Monitor Dashboard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@1.4.0"></script> 
  <style>
    body { font-family: Arial, sans-serif; margin: 1rem; }
    header { margin-bottom: 1rem; }
    #chart-wrap { height: 400px; border: 1px solid #eee; margin-bottom: 1.5rem; padding: 10px; }
    #dbChart { height: 100%; width: 100%; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 0.5rem; border-bottom: 1px solid #eee; text-align: left; }
    .badge { 
        padding: 0.2rem 0.5rem; 
        border-radius: 0.3rem; 
        color: #fff;
        display: inline-block;
    }
    .chart-info { 
        padding: 1rem; 
        background-color: #f8d7da; 
        color: #721c24; 
        border: 1px solid #f5c6cb;
        border-radius: 0.25rem;
    }
  </style>
</head>
<body>
<header>
  <h2>Noise Monitor Dashboard</h2>
  <p>Live data from Arduino</p>
</header>

<div id="chart-wrap">
  <canvas id="dbChart"></canvas>
  <div id="chart-fallback" class="chart-info" style="display:none;">
    ⚠️ Chart unavailable. Data table is still updating.
  </div>
</div>

<section>
  <h3>Recent Events (Last 100)</h3>
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Location</th>
        <th>Decibels</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
</section>

<script>
  // Detect Chart availability (including annotation plugin)
  const ChartAvailable =
    typeof Chart !== 'undefined' &&
    typeof Chart.registry.getPlugin('annotation') !== 'undefined';

  let chart = null;
  const MAX_CHART_POINTS = 60; // Show last 60 seconds

  // Match Arduino thresholds:
  // Quiet < 35 dB, Moderate 35–55 dB, Loud > 55 dB
  function badgeColor(db) {
    if (db > 55) return '#d9534f';   // Loud (Red)
    if (db >= 35) return '#f0ad4e';  // Moderate (Orange)
    return '#5cb85c';                // Quiet (Green)
  }

  if (ChartAvailable) {
    const ctx = document.getElementById('dbChart').getContext('2d');

    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(217, 83, 79, 0.35)');
    gradient.addColorStop(0.6, 'rgba(240, 173, 78, 0.2)');
    gradient.addColorStop(1, 'rgba(92, 184, 92, 0.1)');

    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Noise Level (dB)',
          data: [],
          fill: 'start',
          backgroundColor: gradient,
          borderColor: 'rgb(75, 192, 192)',
          tension: 0.2,
          pointRadius: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        scales: {
          y: {
            title: { display: true, text: 'Decibels (dB)' },
            suggestedMin: 30,
            suggestedMax: 80,
            ticks: { stepSize: 5 }
          },
          x: {
            title: { display: true, text: 'Time (Last 60 Seconds)' },
            ticks: { maxTicksLimit: 10 }
          }
        },
        plugins: {
          annotation: {
            annotations: {
              loudZone: {
                type: 'box',
                yMin: 55,
                yMax: 80,
                backgroundColor: 'rgba(217, 83, 79, 0.15)',
                label: { content: 'Loud', enabled: true }
              },
              moderateZone: {
                type: 'box',
                yMin: 35,
                yMax: 55,
                backgroundColor: 'rgba(240, 173, 78, 0.15)',
                label: { content: 'Moderate', enabled: true }
              },
              quietZone: {
                type: 'box',
                yMin: 30,
                yMax: 35,
                backgroundColor: 'rgba(92, 184, 92, 0.15)',
                label: { content: 'Quiet', enabled: true }
              }
            }
          }
        }
      }
    });
  }

  async function refresh() {
    const res = await fetch('/data?limit=500', { cache: 'no-store' });
    const items = await res.json();
    const chartData = items.slice(-MAX_CHART_POINTS);

    if (ChartAvailable && chart) {
      chart.data.labels = chartData.map(i => i.timestamp.split(' ')[1]);
      chart.data.datasets[0].data = chartData.map(i => i.decibels);
      chart.update();
    }

    const tbody = document.getElementById('rows');
    tbody.innerHTML = '';
    for (let i = items.length - 1; i >= Math.max(0, items.length - 100); i--) {
      const it = items[i];
      tbody.innerHTML += `
        <tr>
          <td>${it.timestamp}</td>
          <td>${it.location || 'N/A'}</td>
          <td><span class="badge" style="background:${badgeColor(it.decibels)};">
            ${it.decibels}
          </span></td>
        </tr>`;
    }
  }

  setInterval(refresh, 1000);
  refresh();
</script>
</body>
</html>
"""
    return render_template_string(html)

if __name__ == '__main__':
    # IMPORTANT: host 0.0.0.0 to accept LAN posts from Arduino
    app.run(host='0.0.0.0', port=5000, debug=True)
