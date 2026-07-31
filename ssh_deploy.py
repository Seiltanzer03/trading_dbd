import paramiko
import sys
import os

host = '94.241.171.182'
user = 'root'
password = os.environ.get("SEILTANZER_SSH_PASSWORD")
if not password:
    raise SystemExit("SEILTANZER_SSH_PASSWORD is required")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=password)

cmd = sys.argv[1] if len(sys.argv) > 1 else 'echo ok'
stdin, stdout, stderr = client.exec_command(cmd)
out = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')
sys.stdout.buffer.write(("STDOUT: " + out + "\n").encode('utf-8', errors='replace'))
sys.stdout.buffer.write(("STDERR: " + err + "\n").encode('utf-8', errors='replace'))
client.close()
