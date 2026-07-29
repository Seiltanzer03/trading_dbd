import paramiko
import sys

host = '94.241.171.182'
user = 'root'
password = 'aJ_UsGuLPFFm,9'

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

