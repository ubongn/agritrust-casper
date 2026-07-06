"""Debug: verify key derivation and signing."""
import json
import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool
from pathlib import Path

KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")

algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
print(f"pbk bytes: {pbk_bytes.hex()} ({len(pbk_bytes)} bytes)")

pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)

# Build minimal deploy
params = create_deploy_parameters(account=pbk, chain_name="casper-test", ttl="30m", gas_price=1)
payment = create_standard_payment(100_000_000_000)
session = ModuleBytes(
    args=[
        DeployArgument("odra_cfg_package_hash_key_name", CL_String("test")),
        DeployArgument("odra_cfg_allow_key_override", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgradable", CL_Bool(True)),
        DeployArgument("odra_cfg_is_upgrade", CL_Bool(False)),
    ],
    module_bytes=b"\x00asm\x01\x00\x00\x00",
)

deploy = create_deploy(params, payment, session)
print(f"Deploy hash: {deploy.hash.hex()}")

deploy.approve(pvk)
print(f"Approvals: {len(deploy.approvals)}")

# Serialize to JSON
deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json = json.loads(deploy_json)

approvals = deploy_json.get("approvals", [])
print(f"JSON approvals: {len(approvals)}")
for a in approvals:
    signer = a.get("signer", "?")
    sig = a.get("signature", "?")
    print(f"  signer: {signer}")
    print(f"  signature: {sig[:40]}...")

# Check header
header = deploy_json.get("header", {})
print(f"\nHeader account: {header.get('account', '?')}")
print(f"Header chain: {header.get('chain_name', '?')}")
print(f"Body hash: {header.get('body_hash', '?')[:20]}...")

# Check what the account field looks like
print(f"\nExpected account: 0202d328d5aebdfaff2e938bd4ef9edcd8d2c63c8d9fb87d77988f789db9404eb78b")
