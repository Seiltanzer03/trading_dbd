import paramiko
import json
import sys

host = '94.241.171.182'
user = 'root'
password = 'aJ_UsGuLPFFm,9'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=password)

# Check IV surface data
stdin, stdout, stderr = client.exec_command("curl -s http://localhost:8790/api/state")
raw = stdout.read().decode('utf-8', errors='replace')
try:
    d = json.loads(raw)
    iv = d.get('iv_surface')
    print("=== IV SURFACE ===")
    print(f"type: {type(iv).__name__}")
    if isinstance(iv, dict):
        print(f"keys: {list(iv.keys())}")
        val = iv.get('value', [])
        print(f"value len: {len(val)}")
        if val:
            print(f"first row keys: {list(val[0].keys()) if isinstance(val[0], dict) else 'not dict'}")
    elif isinstance(iv, list):
        print(f"list len: {len(iv)}")
        if iv:
            print(f"first item keys: {list(iv[0].keys()) if isinstance(iv[0], dict) else type(iv[0]).__name__}")
    else:
        print(f"value: {str(iv)[:200]}")
    
    # Check cone data
    cone = d.get('cone')
    print("\n=== CONE ===")
    print(f"type: {type(cone).__name__}")
    if isinstance(cone, dict):
        print(f"keys: {list(cone.keys())}")
        print(f"available: {cone.get('available')}")
    
    # Check GEX / ridge data  
    print("\n=== RIDGE (GEX source) ===")
    ridge = d.get('ridge')
    if ridge:
        print(f"type: {type(ridge).__name__}")
        if isinstance(ridge, dict):
            print(f"keys: {list(ridge.keys())}")
            snaps = ridge.get('snapshots', [])
            print(f"snapshots: {len(snaps)}")
            if snaps:
                last = snaps[-1]
                gex = last.get('gex', {})
                print(f"last gex keys: {list(gex.keys())}")
                print(f"gex available: {gex.get('available')}")
                strikes = gex.get('strikes', [])
                net = gex.get('net', [])
                print(f"strikes len: {len(strikes)}, net len: {len(net)}")
                if net:
                    import statistics
                    abs_net = [abs(x) for x in net]
                    print(f"net min: {min(net):.0f}, max: {max(net):.0f}")
                    print(f"net median abs: {statistics.median(abs_net):.0f}")
                    # Find top 5
                    indexed = sorted(enumerate(net), key=lambda x: abs(x[1]), reverse=True)[:5]
                    for idx, val in indexed:
                        print(f"  top strike: {strikes[idx]:.2f} -> net: {val:.0f}")
    else:
        print("ridge is None/missing")
    
    # Check correlation
    print("\n=== CORRELATION ===")
    corr = d.get('correlation')
    if corr:
        print(f"type: {type(corr).__name__}")
        if isinstance(corr, dict):
            val = corr.get('value', {})
            if isinstance(val, dict):
                print(f"value keys: {list(val.keys())}")
                assets = val.get('assets', [])
                print(f"assets: {assets}")
    else:
        print("correlation is None/missing")

except json.JSONDecodeError as e:
    print(f"JSON parse error: {e}")
    print(f"Raw (first 500): {raw[:500]}")

client.close()
