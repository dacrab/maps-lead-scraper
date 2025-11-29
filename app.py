"""Flask web dashboard for email scraper."""

import csv
import json
import os
import signal
import subprocess
import time

from flask import Flask, redirect, render_template, request, send_file

from shared import BASE_DIR, CONFIG_FILE, CSV_FILE, TEMPLATE_DIR, Config

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))


def is_scraper_running() -> bool:
    try:
        return bool(subprocess.check_output(["pgrep", "-f", "scraper.py"]).strip())
    except Exception:
        return False


def restart_scraper() -> None:
    try:
        for pid in subprocess.check_output(["pgrep", "-f", "scraper.py"]).decode().split():
            os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass
    time.sleep(1)
    subprocess.Popen(["python3", str(BASE_DIR / "scraper.py"), "--config", str(CONFIG_FILE)])


@app.route("/")
def index():
    config = Config.load()
    data = []
    error = request.args.get("error")

    if CSV_FILE.exists() and CSV_FILE.stat().st_size > 0:
        try:
            with open(CSV_FILE) as f:
                data = list(csv.DictReader(f))
        except Exception as e:
            error = str(e)
    elif not error:
        error = "No data yet"

    return render_template(
        "index.html",
        data=data,
        running=is_scraper_running(),
        config=config.to_dict(),
        error=error,
        success_message=request.args.get("msg"),
    )


@app.route("/update_config", methods=["POST"])
def update_config():
    try:
        Config(
            search_term=request.form.get("search_term", ""),
            locations=[l.strip() for l in request.form.get("locations", "").split(",") if l.strip()],
            max_results_per_query=int(request.form.get("max_results", 10)),
            max_concurrent_pages=int(request.form.get("max_concurrent_pages", 5)),
        ).save()
        restart_scraper()
        return redirect("/?msg=Settings+saved")
    except Exception as e:
        return redirect(f"/?error={e}")


@app.route("/download/<fmt>")
def download(fmt: str):
    if not CSV_FILE.exists():
        return "Not found", 404

    if fmt == "csv":
        return send_file(CSV_FILE, as_attachment=True)

    if fmt == "json":
        json_file = BASE_DIR / "recipients.json"
        with open(CSV_FILE) as f:
            data = list(csv.DictReader(f))
        with open(json_file, "w") as f:
            json.dump(data, f, indent=2)
        return send_file(json_file, as_attachment=True)

    return "Unsupported format", 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
