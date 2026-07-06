"""Deploy AgriTrust contract to Casper Testnet."""
import sys, json, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import requests
import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy, read_wasm
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool

RPC_URL = "https://node.testnet.cspr.cloud/rpc"
AUTH_TOKEN = "55f79117-fc4d-4d60-9956-65423f39a06a"
CHAIN_NAME = "casper-test"
KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = str(Path(__file__).parent.parent / "wasm" / "AgriTrust.wasm")
GAS_PAYMENT = 800_000_000_000
HEADERS = {"Authorization": AUTH_TOKEN, "Content-Type": "application/json"}

print("=" * 60)
print("AgriTrust -> Casper Testnet Deployment")
print("=" * 60)

algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)
account_hex = pycspr.crypto.get_account_key(algo, pbk_bytes).hex()
print(f"Deployer: {account_hex}")

r = requests.get(f"https://api.testnet.cspr.live/accounts/{account_hex}", headers={"Accept": "application/json"}, timeout=15)
balance = int(r.json().get("data", {}).get("balance", "0"))
print(f"Balance: {balance:,} motes = {balance/1e9:.0f} CSPR\n")

wasm_bytes = read_wasm(WASM_PATH)
print(f"WASM size: {len(wasm_bytes):,} bytes")

params = create_deploy_parameters(account=pbk, chain_name=CHAIN_NAME, ttl="30m", gas_price=1)
payment = create_standard_payment(GAS_PAYMENT)
session = ModuleBytes(
    args=[
        DeployArgument("odra_cfg_package_hash_key_name", CL_String("agritrust_contract_package_hash")),
        DeployArgument("odra_cfg_allow_key_override", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgradable", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgrade", CL_Bool(False)),
    ],
    module_bytes=wasm_bytes
)

deploy = create_deploy(params, payment, session)
dhash = deploy.hash.hex()
print(f"Deploy hash: {dhash}")

deploy.approve(pvk)
print("Signed")

deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json = json.loads(deploy_json)

# Fix: pycspr outputs mixed-case hex for secp256k1 keys.
# Recursively lowercase all hex string VALUES (not keys).
def lowercase_hex_values(obj):
    if isinstance(obj, str):
        # Lowercase any string that looks like hex (only hex chars + '0x' prefix)
        s = obj
        if s.startswith("0x"):
            return "0x" + s[2:].lower()
        # Check if it's pure hex (at least 10 chars, only hex chars)
        if len(s) >= 10 and all(c in "0123456789abcdefABCDEF" for c in s):
            return s.lower()
        return obj
    elif isinstance(obj, dict):
        return {k: lowercase_hex_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [lowercase_hex_values(item) for item in obj]
    return obj

deploy_json = lowercase_hex_values(deploy_json)

print(f"\nSubmitting to {CHAIN_NAME}...")
result = requests.post(RPC_URL, headers=HEADERS, json={
    "jsonrpc": "2.0", "id": 1, "method": "account_put_deploy", "params": {"deploy": deploy_json}
}, timeout=120).json()

if "result" in result:
    submitted = result["result"].get("deploy_hash", dhash)
    print(f"Submitted: {submitted}")
    print(f"Explorer: https://testnet.cspr.live/deploy/{submitted}")
    print("\nWaiting", end="", flush=True)

    for _ in range(36):
        time.sleep(5)
        r = requests.get(f"https://api.testnet.cspr.live/deploys/{submitted}", headers={"Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data.get("status") == "processed":
                err = data.get("error_message", "")
                cost = int(data.get("cost", 0))
                if err:
                    print(f"\n\nFAILED: {err}")
                else:
                    print(f"\n\n{'='*60}")
                    print("SUCCESS! AgriTrust deployed!")
                    print(f"{'='*60}")
                    print(f"Deploy: {submitted}")
                    print(f"Cost: {cost/1e9:.1f} CSPR")
                break
        print(".", end="", flush=True)
    else:
        print(" TIMEOUT")

elif "error" in result:
    err = result["error"]
    print(f"\nERROR: {err.get('message')}")
    if "data" in err:
        print(f"  Detail: {str(err['data'])[:500]}")
