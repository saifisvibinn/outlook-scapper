"""
OWA Scraper — Flask + System-tray desktop app.
Run with: python app.py

Opens the UI in your default browser and adds a tray icon so you can quit cleanly.
Works on Python 3.14 without needing .NET / pythonnet.
"""
import os
import sys
import socket
import threading
import uuid
import json
import time
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_file

# ── PyInstaller path helpers ─────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent


def get_resource_path(relative_path):
    """Absolute path to a bundled resource (works in dev and PyInstaller)."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return str(_ROOT / relative_path)


def get_app_dir():
    """Directory for user data (session, senders, output). Always next to the exe."""
    if hasattr(sys, '_MEIPASS'):
        return Path(sys.executable).parent
    return _ROOT


# Playwright browsers: bundled exe uses _MEIPASS; dev uses ./ms-playwright if present
if hasattr(sys, '_MEIPASS'):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(
        sys._MEIPASS, 'ms-playwright'
    )
else:
    _local_browsers = _ROOT / 'ms-playwright'
    if _local_browsers.is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(_local_browsers)

# ── App-level paths ───────────────────────────────────────────────────────────

APP_DIR = get_app_dir()
SESSION_FILE = APP_DIR / 'owa_session.json'
SENDERS_FILE = APP_DIR / 'senders.json'
OUTPUT_DIR = APP_DIR / 'output'

# ── Flask setup ───────────────────────────────────────────────────────────────

app = Flask(
    __name__,
    template_folder=get_resource_path('templates'),
    static_folder=get_resource_path('static'),
)

# In-memory job store: job_id → {status, logs, progress, output_path}
jobs: dict = {}

# Login state (one at a time)
_login_state = {'status': 'idle', 'message': ''}


# ── Utility ───────────────────────────────────────────────────────────────────

def find_free_port(start: int = 5000) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/session')
def get_session():
    return jsonify({'has_session': SESSION_FILE.exists()})


@app.route('/api/login', methods=['POST'])
def login():
    if _login_state['status'] == 'running':
        return jsonify({'error': 'Login already in progress'}), 400

    _login_state['status'] = 'running'
    _login_state['message'] = 'Opening browser window...'

    def do_login():
        try:
            from owa_scraper import login_flow
            login_flow(session_file=SESSION_FILE)
            _login_state['status'] = 'done'
            _login_state['message'] = 'Logged in successfully.'
        except Exception as exc:
            _login_state['status'] = 'error'
            _login_state['message'] = str(exc)

    threading.Thread(target=do_login, daemon=True).start()
    return jsonify({'status': 'started'})


@app.route('/api/login-status')
def login_status():
    return jsonify(_login_state)


@app.route('/api/logout', methods=['POST'])
def logout():
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
    _login_state['status'] = 'idle'
    _login_state['message'] = ''
    return jsonify({'success': True})


@app.route('/api/senders', methods=['GET', 'POST'])
def senders():
    if request.method == 'GET':
        if SENDERS_FILE.exists():
            return jsonify(json.loads(SENDERS_FILE.read_text(encoding='utf-8')))
        return jsonify([])
    data = request.get_json(force=True)
    SENDERS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return jsonify({'success': True})


@app.route('/api/scrape', methods=['POST'])
def scrape():
    if not SESSION_FILE.exists():
        return jsonify({'error': 'No session found. Please log in first.'}), 400

    body = request.get_json(force=True)
    senders_list = body.get('senders', [])
    if not senders_list:
        return jsonify({'error': 'Add at least one sender email address.'}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'status': 'running',
        'logs': [],
        'progress': 0,
        'output_path': None,
        'summary': [],
        'error': None,
    }

    config = {
        'senders': senders_list,
        'mode': body.get('mode', 'count'),         # 'count' or 'months'
        'max_emails': int(body.get('max_emails', 50)),
        'lookback_months': int(body.get('lookback_months', 3)),
        'output_dir': OUTPUT_DIR,
        'session_file': SESSION_FILE,
        'headless': False,  # False = Outlook doesn't detect/block the browser
    }

    def run():
        try:
            from owa_scraper import run_scrape
            total = len(senders_list)

            def progress_cb(msg, sender_idx=None):
                # Skip internal/technical lines from the UI
                _skip = ('[debug]', 'skipped a row', 'attempting', 'retrying', 'waiting')
                if not any(s in msg.lower() for s in _skip):
                    # Humanise the per-email index lines like "  [12] Subject…"
                    import re
                    if re.match(r'\s*\[\d+\]', msg):
                        msg = '    ' + msg.strip()
                    jobs[job_id]['logs'].append(msg)
                if sender_idx is not None:
                    jobs[job_id]['progress'] = int((sender_idx / total) * 90)

            result = run_scrape(config, progress_cb=progress_cb)

            if result is None:
                # Returned None = session expired or no emails
                last_logs = jobs[job_id]['logs']
                if any('expired' in l.lower() or 'log in' in l.lower() for l in last_logs):
                    jobs[job_id]['status'] = 'error'
                    jobs[job_id]['error'] = 'Session expired. Please log out and log in again.'
                    jobs[job_id]['logs'].append('❌ Session expired. Please sign in again.')
                else:
                    jobs[job_id]['status'] = 'done'
                    jobs[job_id]['progress'] = 100
                    last_logs = jobs[job_id]['logs']
                    if any('date range' in l.lower() or 'outside the date' in l.lower() for l in last_logs):
                        jobs[job_id]['logs'].append(
                            '⚠️ No emails found in the selected date range.'
                        )
                    else:
                        jobs[job_id]['logs'].append('⚠️ No emails collected.')
            else:
                output_path, summary = result
                jobs[job_id]['output_path'] = str(output_path)
                jobs[job_id]['summary'] = summary
                jobs[job_id]['status'] = 'done'
                jobs[job_id]['progress'] = 100
                jobs[job_id]['logs'].append(f'✅ Done! {output_path.name}')
        except Exception as exc:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = str(exc)
            jobs[job_id]['logs'].append(f'❌ Error: {exc}')
            jobs[job_id]['progress'] = 0

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/status/<job_id>')
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/api/download/<job_id>')
def download(job_id):
    job = jobs.get(job_id)
    if not job or not job.get('output_path'):
        return jsonify({'error': 'File not ready'}), 404
    return send_file(job['output_path'], as_attachment=True)


@app.route('/api/quit', methods=['POST'])
def quit_app():
    """Fully exit the app (closing the browser tab does not do this)."""

    def shutdown():
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=shutdown, daemon=True).start()
    return jsonify({'success': True})


# ── Entry point ───────────────────────────────────────────────────────────────

def start_flask(port: int):
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


def wait_for_flask(port: int, timeout: float = 15.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def run_tray(port: int):
    """Show a system-tray icon with Open / Quit actions."""
    import webbrowser
    import pystray
    from PIL import Image as PILImage

    url = f'http://127.0.0.1:{port}'

    # Load the icon (works both in dev and packaged)
    icon_path = get_resource_path('icon.png')
    if os.path.exists(icon_path):
        tray_img = PILImage.open(icon_path).convert('RGBA').resize((64, 64))
    else:
        # Fallback: plain teal square
        tray_img = PILImage.new('RGBA', (64, 64), color=(20, 184, 166, 255))

    def on_open(_icon, _item):
        webbrowser.open(url)

    def on_quit(icon, _item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem('Open Outlook Scraper', on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', on_quit),
    )
    icon = pystray.Icon('OutlookScraper', tray_img, 'Outlook Scraper', menu)
    icon.run()


if __name__ == '__main__':
    import webbrowser

    port = find_free_port(5000)

    flask_thread = threading.Thread(target=start_flask, args=(port,), daemon=True)
    flask_thread.start()

    if not wait_for_flask(port):
        print('Flask failed to start in time. Exiting.')
        sys.exit(1)

    # Open browser immediately on launch
    webbrowser.open(f'http://127.0.0.1:{port}')

    # Keep the process alive with a tray icon (or just spin if pystray unavailable)
    try:
        run_tray(port)
    except Exception:
        # Fallback: keep alive without tray
        print(f'OWA Scraper running at http://127.0.0.1:{port}  (Ctrl+C to quit)')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

