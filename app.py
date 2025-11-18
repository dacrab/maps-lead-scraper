from flask import Flask, render_template, send_file, request, redirect
import pandas as pd
from pathlib import Path
import subprocess
import os
import json
import signal
import time

app = Flask(__name__)

# Constants
BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / 'recipients.csv'
CONFIG_FILE = BASE_DIR / 'config.json'
LOCK_FILE = BASE_DIR / 'scraper.lock'
TEMPLATE_DIR = BASE_DIR / 'templates'

app.template_folder = str(TEMPLATE_DIR)

def get_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def is_scraper_running():
    # Simple check if python scraper process is active
    try:
        res = subprocess.check_output(["pgrep", "-f", "scraper.py"])
        return bool(res.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def restart_scraper():
    """Kills existing scraper process and starts a new one."""
    # 1. Kill existing
    try:
        pids = subprocess.check_output(["pgrep", "-f", "scraper.py"]).decode().splitlines()
        for pid in pids:
            if pid:
                os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass # No process found
    
    # 2. Start new
    time.sleep(1)
    # Inherit stdout/stderr so logs show up in Railway/Docker logs
    subprocess.Popen(
        ["python3", str(BASE_DIR / "scraper.py"), "--config", str(CONFIG_FILE)],
        cwd=str(BASE_DIR)
    )

@app.route('/')
def index():
    running = is_scraper_running()
    config = get_config()
    success_message = request.args.get('msg')
    
    if not CSV_FILE.exists():
        return render_template('index.html', data=None, running=running, config=config, error="Results file not found yet.", success_message=success_message)
    
    try:
        # Handle empty or malformed file
        if CSV_FILE.stat().st_size == 0:
             return render_template('index.html', data=None, running=running, config=config, error="Data file is empty (initializing...)", success_message=success_message)
             
        df = pd.read_csv(CSV_FILE)
        # Convert to HTML table
        table_html = df.to_html(classes='table table-hover', index=False, border=0)
        return render_template('index.html', data=table_html, running=running, config=config, success_message=success_message)
    except pd.errors.EmptyDataError:
        return render_template('index.html', data=None, running=running, config=config, error="Data file is empty.", success_message=success_message)
    except Exception as e:
        return render_template('index.html', data=None, running=running, config=config, error=f"Error reading data: {str(e)}", success_message=success_message)

@app.route('/update_config', methods=['POST'])
def update_config():
    try:
        new_config = get_config()
        
        # Update fields
        new_config['search_term'] = request.form.get('search_term', '')
        locations_str = request.form.get('locations', '')
        new_config['locations'] = [loc.strip() for loc in locations_str.split(',') if loc.strip()]
        new_config['max_results_per_query'] = int(request.form.get('max_results', 10))
        new_config['max_thread_workers'] = int(request.form.get('max_thread_workers', 3))
        
        # Save
        with open(CONFIG_FILE, 'w') as f:
            json.dump(new_config, f, indent=4)
            
        # Restart Scraper
        restart_scraper()
        
        return redirect('/?msg=Settings+saved.+Scraper+restarted.')
    except Exception as e:
        return redirect(f'/?error=Failed+to+update+config:+{str(e)}')

@app.route('/download/<format>')
def download(format):
    if not CSV_FILE.exists():
        return "File not found", 404
    
    df = pd.read_csv(CSV_FILE)
    
    if format == 'csv':
        return send_file(CSV_FILE, as_attachment=True)
    elif format == 'json':
        json_file = BASE_DIR / 'recipients.json'
        df.to_json(json_file, orient='records')
        return send_file(json_file, as_attachment=True)
    elif format == 'excel':
        xlsx_file = BASE_DIR / 'recipients.xlsx'
        df.to_excel(xlsx_file, index=False)
        return send_file(xlsx_file, as_attachment=True)
    
    return "Invalid format", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
