"""
Deploy AgriTrust contract to Casper Testnet via pycspr.

Adapted from PayMesh deploy.py — same pycspr approach, same deployer account.
AgriTrust requires init args: protocol_fee_bps (U32), grace_period_secs (U64).
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

import requests
import pycspr
from pycspr import (
    create_deploy_parameters,
    create_standard_payment,
    create_deploy,
    read_wasm,
)
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool, CL_U32, CL_U64

# ─── Configuration ───────────────────────────────────────────────────────────

RPC_URL = "https://node.testnet.cspr.cloud/rpc"
AUTH_TOKEN = "55f79117-fc4d-4d60-9956-65423f39a06a"
CHAIN_NAME = "casper-test"
KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = Path(__file__).parent.parent / "contracts" / "wasm" / "AgriTrust.wasm"
GAS_PAYMENT = 800_000_000_000  # 800 CSPR max (block gas limit is 812.5 CSPR)

HEADERS = {"Authorization": AUTH_TOKEN, "Content-Type": "application/json"}


def load_keys():
    """Create FRESH key pair objects (avoids pycspr state bugs)."""
    algo = KeyAlgorithm.SECP256K1
    pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
    pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
    pbk = create_public_key(algo, pbk_bytes)
    account_hex = pycspr.crypto.get_account_key(algo, pbk_bytes).hex()
    return pvk, pbk, account_hex


def check_balance(account_hex):
    url = f"https://api.testnet.cspr.live/accounts/{account_hex}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
    if r.status_code == 200:
        balance = int(r.json().get("data", {}).get("balance", "0"))
        return balance
    return 0


def rpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        payload["params"] = params
    return requests.post(RPC_URL, headers=HEADERS, json=payload, timeout=120).json()


def build_deploy_args():
    """Odra cfg args + AgriTrust init args."""
    return [
        # ─── Odra framework required args ───
        DeployArgument(
            "odra_cfg_package_hash_key_name",
            CL_String("agritrust_package_hash"),
        ),
        DeployArgument("odra_cfg_allow_key_override", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgradable", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgrade", CL_Bool(False)),
        # ─── AgriTrust init args ───
        DeployArgument("protocol_fee_bps", CL_U32(50)),         # 0.5% protocol fee
        DeployArgument("grace_period_secs", CL_U64(259200)),    # 3 days grace period
    ]


def deploy_contract(wasm_bytes, pbk, pvk):
    """Build, sign, and submit the contract deployment."""
    print(f"Building deploy ({len(wasm_bytes):,} bytes WASM)...")

    params = create_deploy_parameters(
        account=pbk,
        chain_name=CHAIN_NAME,
        ttl="30m",
        gas_price=1,
    )

    payment = create_standard_payment(GAS_PAYMENT)
    args = build_deploy_args()
    session = ModuleBytes(args=args, module_bytes=wasm_bytes)

    deploy = create_deploy(params, payment, session)
    deploy_hash = deploy.hash.hex()
    print(f"  Deploy hash: {deploy_hash}")

    # Sign with fresh key
    deploy.approve(pvk)
    print(f"  Signed with secp256k1")

    # Serialize to JSON
    deploy_json = pycspr.to_json(deploy)
    if isinstance(deploy_json, str):
        deploy_json = json.loads(deploy_json)

    # Normalize all hex strings to lowercase (pycspr produces mixed-case hex
    # which can cause Casper node signature verification to fail)
    def lowercase_hex(obj):
        """Recursively lowercase all hex string values."""
        if isinstance(obj, str):
            # Check if it looks like a hex string
            if obj and all(c in '0123456789abcdefABCDEF' for c in obj) and len(obj) >= 2:
                return obj.lower()
            return obj
        elif isinstance(obj, dict):
            return {k: lowercase_hex(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [lowercase_hex(item) for item in obj]
        return obj

    deploy_json = lowercase_hex(deploy_json)
    # Verify hash matches
    json_hash = deploy_json.get("hash", "")
    print(f"  JSON hash (lowercased): {json_hash[:32]}...")

    # Submit
    print(f"  Submitting to {CHAIN_NAME}...")
    result = rpc("account_put_deploy", {"deploy": deploy_json})

    if "result" in result:
        dhash = result["result"].get("deploy_hash", deploy_hash)
        print(f"  [OK] Submitted! Hash: {dhash}")
        return dhash
    elif "error" in result:
        err = result["error"]
        print(f"  [ERR] RPC error: {err.get('message', str(err))}")
        if "data" in err:
            print(f"        Detail: {str(err['data'])[:500]}")
        return None
    else:
        print(f"  [WARN] Unexpected: {json.dumps(result)[:300]}")
        return None


def check_deploy_status(dhash, timeout=180):
    """Poll CSPR.live REST API for deploy status."""
    url = f"https://api.testnet.cspr.live/deploys/{dhash}"
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            status = data.get("status", "")
            if status == "processed":
                err = data.get("error_message", "")
                block = data.get("block_hash", "?")
                cost = data.get("cost", "?")
                contract_hash = data.get("contract_hash")
                if err:
                    print(f"  [FAIL] Error: {err}")
                    if cost != "?":
                        print(f"         Cost: {int(cost)/1e9:.1f} CSPR")
                    return False, data
                else:
                    print(f"  [OK] Confirmed! Block: {block[:16]}...")
                    print(f"       Cost: {int(cost)/1e9:.1f} CSPR")
                    if contract_hash:
                        print(f"       Contract hash: {contract_hash}")
                    return True, data
            elif status in ("expired", "failed"):
                print(f"  [FAIL] Status: {status}")
                return False, data
        print(".", end="", flush=True)
        time.sleep(5)
    print(" [TIMEOUT]")
    return None, None


def main():
    pvk, pbk, account_hex = load_keys()
    print(f"Deployer account: {account_hex}")
    print(f"Algorithm:        secp256k1\n")

    # Check balance
    balance = check_balance(account_hex)
    print(f"Balance: {balance / 1e9:.2f} CSPR")
    if balance < 50_000_000_000:
        print(f"ERROR: Need at least 50 CSPR, have {balance / 1e9:.2f}")
        sys.exit(1)

    # Load WASM
    if not WASM_PATH.exists():
        print(f"ERROR: WASM not found at {WASM_PATH}")
        sys.exit(1)
    wasm_bytes = WASM_PATH.read_bytes()
    print(f"WASM: {WASM_PATH.name} ({len(wasm_bytes):,} bytes)\n")

    print("=" * 60)
    print(f"AgriTrust -> Casper Testnet ({GAS_PAYMENT/1e9:.0f} CSPR gas limit)")
    print("=" * 60)

    # Deploy
    dhash = deploy_contract(wasm_bytes, pbk, pvk)
    if not dhash:
        print("\n[FAILED] Deploy submission failed.")
        sys.exit(1)

    # Wait for confirmation
    print(f"\nWaiting for confirmation ({dhash[:16]}...)...")
    success, data = check_deploy_status(dhash)

    if success:
        cost = int(data.get("cost", 0)) / 1e9
        print(f"\n{'=' * 60}")
        print(f"[SUCCESS] AgriTrust deployed to Casper Testnet!")
        print(f"  Deploy hash: {dhash}")
        print(f"  Gas cost:    {cost:.2f} CSPR")
        contract_hash = data.get("contract_hash")
        if contract_hash:
            print(f"  Contract:    {contract_hash}")
        # New balance
        new_bal = check_balance(account_hex)
        print(f"  Remaining:   {new_bal / 1e9:.2f} CSPR")
        print(f"  Explorer:    https://testnet.cspr.live/deploys/{dhash}")
        print(f"{'=' * 60}")

        # Save deployment info
        deploy_info = {
            "contract": "AgriTrust",
            "deploy_hash": dhash,
            "block_hash": data.get("block_hash"),
            "contract_hash": contract_hash,
            "cost_cspr": cost,
            "init_args": {
                "protocol_fee_bps": 50,
                "grace_period_secs": 259200,
            },
            "deployer": "0203358a59f8208973c70520fbc0ac07776dd3e2b80c10c0c7c164b9122bbc25d9fc",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        info_path = Path(__file__).parent.parent / "deployment.json"
        info_path.write_text(json.dumps(deploy_info, indent=2))
        print(f"\nDeployment info saved to {info_path}")
    else:
        print(f"\n[FAILED] Deploy not confirmed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
