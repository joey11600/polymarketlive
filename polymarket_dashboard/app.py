import os
import json
import subprocess
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
from functools import wraps
import paramiko
import base64

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configuration
SCANNER_HOST = os.environ.get('SCANNER_HOST', '3.252.57.88')
SCANNER_USER = os.environ.get('SCANNER_USER', 'ubuntu')
SCANNER_PATH = os.environ.get('SCANNER_PATH', '/home/ubuntu/cex_lag_trader')
SSH_KEY_B64 = os.environ.get('SSH_PRIVATE_KEY', '')
DASHBOARD_USER = os.environ.get('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASS = os.environ.get('DASHBOARD_PASSWORD', 'admin')

# Decode SSH key
def get_ssh_key():
    if SSH_KEY_B64:
        return base64.b64decode(SSH_KEY_B64).decode('utf-8')
    return None

# Basic authentication
def check_auth(username, password):
    return username == DASHBOARD_USER and password == DASHBOARD_PASS

def authenticate():
    return jsonify({'error': 'Authentication required'}), 401

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# SSH command execution
def run_ssh_command(command):
    try:
        key_str = get_ssh_key()
        if not key_str:
            return {'error': 'SSH key not configured'}
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key = paramiko.RSAKey.from_private_key(file_obj=open(key_str) if os.path.exists(key_str) else __import__('io').StringIO(key_str))
        
        ssh.connect(SCANNER_HOST, username=SCANNER_USER, pkey=key, timeout=10)
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        ssh.close()
        
        return {'output': output, 'error': error, 'success': not error}
    except Exception as e:
        return {'error': str(e), 'success': False}

@app.route('/')
@requires_auth
def index():
    return render_template('index.html')

@app.route('/api/status')
@requires_auth
def api_status():
    result = run_ssh_command(f'ps aux | grep prob_edge_scanner.py | grep -v grep')
    running = 'prob_edge_scanner.py' in result.get('output', '')
    
    return jsonify({
        'running': running,
        'uptime': '0h 0m',
        'last_activity': datetime.now(timezone.utc).isoformat(),
        'ticks': 0
    })

@app.route('/api/trades')
@requires_auth
def api_trades():
    result = run_ssh_command(f'cd {SCANNER_PATH} && tail -20 live_trades.jsonl')
    trades = []
    if result.get('success'):
        for line in result['output'].strip().split('\n'):
            if line:
                try:
                    trades.append(json.loads(line))
                except:
                    pass
    return jsonify({'trades': trades})

@app.route('/api/balance')
@requires_auth
def api_balance():
    result = run_ssh_command(f'cd {SCANNER_PATH} && python3 show_wallet.py 2>&1 | grep "Wallet Balance"')
    balance = 0.0
    if result.get('success'):
        try:
            balance_str = result['output'].split('$')[1].split()[0]
            balance = float(balance_str)
        except:
            pass
    return jsonify({'balance': balance})

@app.route('/api/pnl')
@requires_auth
def api_pnl():
    result = run_ssh_command(f'cd {SCANNER_PATH} && tail -50 settlements.jsonl')
    wins = 0
    losses = 0
    total_pnl = 0.0
    
    if result.get('success'):
        for line in result['output'].strip().split('\n'):
            if line:
                try:
                    settlement = json.loads(line)
                    pnl = settlement.get('pnl_realized_down_buy', 0)
                    if pnl > 0:
                        wins += 1
                    elif pnl < 0:
                        losses += 1
                    total_pnl += pnl
                except:
                    pass
    
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    return jsonify({
        'today_pnl': total_pnl,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses
    })

@app.route('/api/start', methods=['POST'])
@requires_auth
def api_start():
    data = request.get_json()
    max_trades = data.get('max_trades', 10)
    duration = data.get('duration', 90)
    
    cmd = f'cd {SCANNER_PATH} && nohup python3 prob_edge_scanner.py --live_trading --best_side_mode --duration_min {duration} > scanner.log 2>&1 &'
    result = run_ssh_command(cmd)
    
    return jsonify({'success': result.get('success', False), 'message': 'Scanner started' if result.get('success') else 'Failed to start'})

@app.route('/api/stop', methods=['POST'])
@requires_auth
def api_stop():
    result = run_ssh_command('pkill -f prob_edge_scanner.py')
    return jsonify({'success': True, 'message': 'Scanner stopped'})

@app.route('/api/logs')
@requires_auth
def api_logs():
    result = run_ssh_command(f'cd {SCANNER_PATH} && tail -100 scanner.log')
    logs = result.get('output', '').split('\n') if result.get('success') else []
    return jsonify({'logs': logs})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
