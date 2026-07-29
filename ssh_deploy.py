import paramiko
import sys

def run_ssh(cmd):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('94.241.171.182', username='root', password='aJ_UsGuLPFFm,9')
        stdin, stdout, stderr = client.exec_command(cmd)
        print("STDOUT:")
        print(stdout.read().decode('utf-8'))
        print("STDERR:")
        print(stderr.read().decode('utf-8'))
    except Exception as e:
        print("ERROR:", str(e))
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_ssh(sys.argv[1])
