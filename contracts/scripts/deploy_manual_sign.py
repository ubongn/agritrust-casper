"""Deploy AgriTrust contract with manual secp256k1 signing to avoid pycspr signing bugs."""
import json
import time
import requests
import hashlib

import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy, read_wasm
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool
from pathlib import Path
from ecdsa import SigningKey, SECP256k1

# --- Config ---
RPC_URL = "https://node.testnet.cspr.cloud/rpc"
HEADERS = {"Content-Type": "application/json", "Authorization": "Bearer 55f79117-fc4d-4d60-9956-65423f39a06a"}
CHAIN_NAME = "casper-test"
GAS_MOTES = 800_000_000_000  # 800 CSPR

KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = str(Path(__file__).parent.parent / "wasm" / "AgriTrust.wasm")

# --- Load keys ---
algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)

print(f"Deployer public key: {pbk_bytes.hex()}")
print(f"Private key: {pvk_bytes.hex()[:20]}...")

# Check balance
account_hash = pycspr.crypto.get_account_hash(pycspr.crypto.get_account_key(algo, pbk_bytes)).hex()
state = requests.post(RPC_URL, headers=HEADERS, json={
    "jsonrpc": "2.0", "id": 1,
    "method": "state_get_account_info",
    "params": {"public_key": pbk_bytes.hex(), "block_identifier": {"Height": 0}}
}, timeout=30).json()
if "result" in state:
    bal = int(state["result"]["account"]["main_purse_balance"])
    print(f"Balance: {bal:,} motes = {bal/1e9:.0f} CSPR")

# --- Read WASM ---
wasm_bytes = read_wasm(WASM_PATH)
print(f"WASM size: {len(wasm_bytes):,} bytes")

# --- Create deploy ---
params = create_deploy_parameters(account=pbk, chain_name=CHAIN_NAME, ttl="30m", gas_price=1)
payment = create_standard_payment(GAS_MOTES)
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
print(f"Deploy hash (pycspr): {dhash}")

# --- Manual signing ---
# Casper uses secp256k1 signatures in a specific format:
# The signature is 65 bytes: recovery_byte (1) + r (32) + s (32)
# Casper expects recovery_id in [0,1] (uncompressed key)
hash_bytes = deploy.hash
print(f"Hash bytes: {hash_bytes.hex()}")

# Sign with ecdsa library using deterministic K (RFC 6979)
sk = SigningKey.from_string(pvk_bytes, curve=SECP256k1)
# Get recoverable signature
sig = sk.sign_recoverable(hash_bytes, hashfunc=hashlib.sha256, sigencode=__import__('ecdsa').util.sigencode_string_canonize)
r_bytes = sig[:32]
s_bytes = sig[32:64]
recovery_id = sig[64]
print(f"Recovery ID: {recovery_id}")

# Casper secp256k1 signature format: recovery_id (1 byte) + r (32 bytes) + s (32 bytes) = 65 bytes
if recovery_id > 1:
    recovery_id = recovery_id - 1 if recovery_id >= 2 else recovery_id
    print(f"Adjusted recovery ID: {recovery_id}")

sig_bytes = bytes([recovery_id]) + r_bytes + s_bytes
sig_hex = sig_bytes.hex()
print(f"Signature: {sig_hex[:40]}...")
print(f"Signature length: {len(sig_bytes)} bytes")

# --- Build deploy JSON manually ---
deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json = json.loads(deploy_json)

# Replace the approval with our manual signature
pubkey_hex = pbk_bytes.hex()
deploy_json["approvals"] = [{
    "signature": "01" + sig_hex,  # 01 = secp256k1 marker prefix
    "signer": pubkey_hex
}]

# Lowercase all hex values
def lowercase_hex_values(obj):
    if isinstance(obj, str):
        s = obj
        if s.startswith("0x"):
            return "0x" + s[2:].lower()
        if len(s) >= 10 and all(c in "0123456789abcdefABCDEF" for c in s):
            return s.lower()
        return obj
    elif isinstance(obj, dict):
        return {k: lowercase_hex_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [lowercase_hex_values(item) for item in obj]
    return obj

deploy_json = lowercase_hex_values(deploy_json)

# --- Submit ---
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
