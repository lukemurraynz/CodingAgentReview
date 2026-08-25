import subprocess


def ping(host):
    return subprocess.run(f'ping -c1 {host}', shell=True, capture_output=True)
