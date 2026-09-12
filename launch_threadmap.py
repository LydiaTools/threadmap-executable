"""User-clicked launcher: reuse the local instance or start one, then open it."""
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8771/'


def ready():
    try:
        with urllib.request.urlopen(URL + 'api/state', timeout=1) as response:
            data = json.load(response)
        return isinstance(data.get('nodes'), list) and isinstance(data.get('limits'), dict)
    except (OSError, ValueError, AttributeError, urllib.error.URLError):
        return False


def main():
    if ready():
        subprocess.run(['/usr/bin/open', URL], check=True)
        return 0
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', 8771)) == 0:
            print('8771 端口已有其他程序占用，未结束任何进程。请在总入口反馈。')
            return 1
    process = subprocess.Popen([sys.executable, str(ROOT / 'threadmap_server.py'), '--port', '8771'], cwd=ROOT)
    def stop(signum, frame):
        if process.poll() is None:
            process.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        for _ in range(50):
            if ready():
                subprocess.run(['/usr/bin/open', URL], check=True)
                print('任务脉络已打开。关闭此终端会停止本次服务；下次双击可重新启动。')
                return process.wait()
            if process.poll() is not None:
                return process.returncode
            time.sleep(.1)
        print('启动未就绪，保留原状态文件；请在总入口反馈。')
        return 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
