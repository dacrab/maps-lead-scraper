from flask import Flask, render_template_string, send_file, jsonify
import pandas as pd
import os
import threading
import subprocess
import sys

app = Flask(__name__)
CSV_FILE = 'recipients.csv'
LOCK_FILE = 'scraper.lock'

# HTML Template with embedded CSS for modern UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scraped Data Results</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
    <style>
        body { background-color: #f8f9fa; }
        .container { margin-top: 40px; max-width: 90%; }
        .card { border: none; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .card-header { background: white; border-bottom: 1px solid #eee; padding: 20px; border-radius: 12px 12px 0 0 !important; }
        .btn-download { margin-right: 10px; }
        .status-badge { font-size: 0.9em; }
        .dataTables_wrapper { padding: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <div>
                    <h2 class="mb-0">📧 Email Scraper Results</h2>
                    <p class="text-muted mb-0">View and manage your scraped contacts</p>
                </div>
                <div>
                    {% if running %}
                        <span class="badge bg-warning text-dark status-badge">Running...</span>
                    {% else %}
                        <span class="badge bg-success status-badge">Completed</span>
                    {% endif %}
                </div>
            </div>
            <div class="card-body">
                {% if error %}
                    <div class="alert alert-warning">{{ error }}</div>
                {% endif %}

                <div class="mb-3">
                    <a href="/download/csv" class="btn btn-primary btn-download">Download CSV</a>
                    <a href="/download/json" class="btn btn-secondary btn-download">Download JSON</a>
                    <a href="/download/excel" class="btn btn-success btn-download">Download Excel</a>
                </div>

                {% if data %}
                    <div class="table-responsive">
                        {{ data | safe }}
                    </div>
                {% else %}
                    <div class="text-center py-5">
                        <div class="spinner-border text-primary" role="status"></div>
                        <p class="mt-2">Waiting for data... (Refresh page)</p>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>

    <script src="https://code.jquery.com/jquery-3.7.0.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            $('table').addClass('table table-hover table-striped');
            $('table').DataTable({
                "order": [[ 0, "asc" ]],
                "pageLength": 25
            });
        });
        
        // Auto-refresh if running
        {% if running %}
        setTimeout(function(){
           window.location.reload(1);
        }, 5000);
        {% endif %}
    </script>
</body>
</html>
"""

def is_scraper_running():
    # Simple check if python scraper process is active
    try:
        # This is a bit rough, but works for single container
        res = subprocess.check_output(["pgrep", "-f", "scraper.py"])
        return bool(res.strip())
    except subprocess.CalledProcessError:
        return False

@app.route('/')
def index():
    running = is_scraper_running()
    
    if not os.path.exists(CSV_FILE):
        return render_template_string(HTML_TEMPLATE, data=None, running=running, error="Results file not found yet.")
    
    try:
        df = pd.read_csv(CSV_FILE)
        # Convert to HTML table
        table_html = df.to_html(classes='table', index=False, border=0)
        return render_template_string(HTML_TEMPLATE, data=table_html, running=running)
    except Exception as e:
        return render_template_string(HTML_TEMPLATE, data=None, running=running, error=f"Error reading data: {str(e)}")

@app.route('/download/<format>')
def download(format):
    if not os.path.exists(CSV_FILE):
        return "File not found", 404
    
    df = pd.read_csv(CSV_FILE)
    
    if format == 'csv':
        return send_file(CSV_FILE, as_attachment=True)
    elif format == 'json':
        json_file = 'recipients.json'
        df.to_json(json_file, orient='records')
        return send_file(json_file, as_attachment=True)
    elif format == 'excel':
        xlsx_file = 'recipients.xlsx'
        df.to_excel(xlsx_file, index=False)
        return send_file(xlsx_file, as_attachment=True)
    
    return "Invalid format", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
