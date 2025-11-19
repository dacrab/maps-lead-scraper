from flask import Flask, render_template, send_file, request, redirect
import csv
from pathlib import Path
import subprocess
import os
import json
import signal
import time

# Constants
BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / 'recipients.csv'
CONFIG_FILE = BASE_DIR / 'config.json'
LOCK_FILE = BASE_DIR / 'scraper.lock'
TEMPLATE_DIR = BASE_DIR / 'templates'

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

def get_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def is_scraper_running():
    try:
        res = subprocess.check_output(["pgrep", "-f", "scraper.py"])
        return bool(res.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def restart_scraper():
    try:
        pids = subprocess.check_output(["pgrep", "-f", "scraper.py"]).decode().splitlines()
        for pid in pids:
            if pid:
                os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass
    
    time.sleep(1)
    subprocess.Popen(
        ["python3", str(BASE_DIR / "scraper.py"), "--config", str(CONFIG_FILE)],
        cwd=str(BASE_DIR)
    )

@app.route('/')
def index():
    running = is_scraper_running()
    config = get_config()
    success_message = request.args.get('msg')
    
    data_list = []
    if CSV_FILE.exists() and CSV_FILE.stat().st_size > 0:
        try:
            with open(CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                data_list = list(reader)
        except Exception as e:
            return render_template('index.html', data=[], running=running, config=config, error=f"Error reading data: {e}", success_message=success_message)
    else:
        return render_template('index.html', data=[], running=running, config=config, error="Data file is empty or missing.", success_message=success_message)

    return render_template('index.html', data=data_list, running=running, config=config, success_message=success_message)

@app.route('/update_config', methods=['POST'])
def update_config():
    try:
        new_config = get_config()
        new_config['search_term'] = request.form.get('search_term', '')
        locations_str = request.form.get('locations', '')
        new_config['locations'] = [loc.strip() for loc in locations_str.split(',') if loc.strip()]
        new_config['max_results_per_query'] = int(request.form.get('max_results', 10))
        new_config['max_concurrent_pages'] = int(request.form.get('max_concurrent_pages', 5))
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(new_config, f, indent=4)
            
        restart_scraper()
        return redirect('/?msg=Settings+saved.+Scraper+restarted.')
    except Exception as e:
        return redirect(f'/?error=Failed+to+update+config:+{str(e)}')

@app.route('/download/<format>')
def download(format):
    if not CSV_FILE.exists():
        return "File not found", 404
    
    if format == 'csv':
        return send_file(CSV_FILE, as_attachment=True)
    elif format == 'json':
        json_file = BASE_DIR / 'recipients.json'
        data = []
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            data = list(reader)
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        return send_file(json_file, as_attachment=True)
    
    return "Format not supported in minimal mode", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
