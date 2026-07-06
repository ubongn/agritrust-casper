"""Test: minimal transfer to verify secp256k1 signing works."""
import json, time, requests
import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import Transfer

RPC_URL = "https://node.testnet.cspr.cloud/rpc"
HEADERS = {"Authorization": "Bearer 55f79117-fc4d-4d60-9956-65423f39a06a", "Content-Type": "application/json"}
CHAIN_NAME = "casper-test"
KEY_PEM = str(r"C:\Users\Sabiedu\.qwenpaw\workspaces\hack_1\agritrust-casper\contracts\keys\deployer_secret_key.pem")

algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)

account_hex = pycspr.crypto.get_account_key(algo, pbk_bytes).hex()
print(f"Account: {account_hex}")

# Self-transfer of 1 CSPR to test signing
params = create_deploy_parameters(account=pbk, chain_name=CHAIN_NAME, ttl="30m", gas_price=1)
deploy = pycspr.create_transfer(params=params, amount=1_000_000_000, target=pbk_bytes)
print(f"Deploy hash: {deploy.hash.hex()}")

deploy.approve(pvk)
print("Signed")

deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json = json.loads(deploy_json)

# Lowercase all hex values
def lc(obj):
    if isinstance(obj, str):
        if len(obj) >= 10 and all(c in "0123456789abcdefABCDEF" for c in obj):
            return obj.lower()
        return obj
    elif isinstance(obj, dict):
        return {k: lc(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [lc(i) for i in obj]
    return obj

deploy_json = lc(deploy_json)

print(f"\nSubmitting...")
result = requests.post(RPC_URL, headers=HEADERS, json={
    "jsonrpc": "2.0", "id": 1, "method": "account_put_deploy", "params": {"deploy": deploy_json}
}, timeout=60).json()

print(json.dumps(result, indent=2)[:500])

if "result" in result:
    dhash = result["result"]["deploy_hash"]
    print(f"\nDeploy accepted! Hash: {dhash}")
    print(f"Explorer: https://testnet.cspr.live/deploy/{dhash}")
    # Wait for result
    for _ in range(12):
        time.sleep(5)
        r = requests.get(f"https://api.testnet.cspr.live/deploys/{dhash}", headers={"Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data.get("status") == "processed":
                print(f"Status: {data.get('status')}, Error: {data.get('error_message', 'none')}")
                break
        print(".", end="", flush=True)
