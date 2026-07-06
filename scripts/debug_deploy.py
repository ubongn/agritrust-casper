"""
Debug: inspect deploy construction + signing + JSON serialization.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

import pycspr
from pycspr import (
    create_deploy_parameters,
    create_standard_payment,
    create_deploy,
)
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool, CL_U32, CL_U64

RPC_URL = "https://node.testnet.cspr.cloud/rpc"
AUTH_TOKEN = "55f79117-fc4d-4d60-9956-65423f39a06a"
CHAIN_NAME = "casper-test"
KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = Path(__file__).parent.parent / "contracts" / "wasm" / "AgriTrust.wasm"
GAS_PAYMENT = 800_000_000_000

# Load keys
algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)
print(f"Private key: {pvk_bytes.hex()[:16]}...")
print(f"Public key:  {pbk_bytes.hex()[:16]}...")
print(f"pvk.pvk:     {pvk.pvk.hex()[:16] if hasattr(pvk, 'pvk') else 'N/A'}")
print(f"pvk.pbk:     {pvk.pbk.hex()[:16] if hasattr(pvk, 'pbk') else 'N/A'}")

# Load WASM
wasm_bytes = WASM_PATH.read_bytes()
print(f"\nWASM: {len(wasm_bytes):,} bytes")

# Build deploy
params = create_deploy_parameters(
    account=pbk,
    chain_name=CHAIN_NAME,
    ttl="30m",
    gas_price=1,
)

payment = create_standard_payment(GAS_PAYMENT)

args = [
    DeployArgument("odra_cfg_package_hash_key_name", CL_String("agritrust_package_hash")),
    DeployArgument("odra_cfg_allow_key_override", CL_Bool(True)),
    DeployArgument("odra_cfg_is_upgradable", CL_Bool(True)),
    DeployArgument("odra_cfg_is_upgrade", CL_Bool(False)),
    DeployArgument("protocol_fee_bps", CL_U32(50)),
    DeployArgument("grace_period_secs", CL_U64(259200)),
]

session = ModuleBytes(args=args, module_bytes=wasm_bytes)

deploy = create_deploy(params, payment, session)
deploy_hash = deploy.hash.hex()
print(f"\nDeploy hash (pre-approve): {deploy_hash}")

# Approve
deploy.approve(pvk)
print(f"Approvals after sign: {len(deploy.approvals)}")
for i, ap in enumerate(deploy.approvals):
    print(f"  Approval {i}:")
    signer_str = str(ap.signer)
    print(f"    signer:    {signer_str[:64]}...")
    sig_str = ap.signature if isinstance(ap.signature, str) else ap.signature.hex() if hasattr(ap.signature, 'hex') else str(ap.signature)
    print(f"    signature: {sig_str[:64]}...")
    print(f"    sig len:   {len(sig_str)}")

# Serialize
deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json_parsed = json.loads(deploy_json)
else:
    deploy_json_parsed = deploy_json

# Check key structure
print(f"\nJSON top-level keys: {list(deploy_json_parsed.keys())}")
print(f"Payment keys: {list(deploy_json_parsed.get('payment', {}).keys())}")
print(f"Session keys: {list(deploy_json_parsed.get('session', {}).keys())}")
sess = deploy_json_parsed.get('session', {})
print(f"Session.ModuleBytes args count: {len(sess.get('ModuleBytes', {}).get('args', []))}")
for i, arg in enumerate(sess.get('ModuleBytes', {}).get('args', [])):
    print(f"  Arg {i}: name={arg[0]}, cl_type={arg[1].get('cl_type')}, parsed={arg[1].get('parsed')}")

print(f"\nHeader: {json.dumps(deploy_json_parsed.get('header', {}), indent=2)}")
print(f"Approvals: {json.dumps(deploy_json_parsed.get('approvals', []), indent=2)[:500]}")

# Verify hash from JSON
body_hash_str = deploy_json_parsed.get('header', {}).get('body_hash', '')
print(f"\nBody hash from JSON: {body_hash_str[:32]}...")
print(f"Deploy hash from JSON: {deploy_json_parsed.get('hash', 'N/A')[:32]}...")
