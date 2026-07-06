"""Debug: dump the full deploy JSON."""
import json
import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy, read_wasm
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool
from pathlib import Path

KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = str(Path(__file__).parent.parent / "wasm" / "AgriTrust.wasm")

algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)

wasm_bytes = read_wasm(WASM_PATH)

params = create_deploy_parameters(account=pbk, chain_name="casper-test", ttl="30m", gas_price=1)
payment = create_standard_payment(800_000_000_000)
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
deploy.approve(pvk)

deploy_json = pycspr.to_json(deploy)
if isinstance(deploy_json, str):
    deploy_json = json.loads(deploy_json)

# Print the full JSON without the module_bytes
dj = json.loads(json.dumps(deploy_json))
mb = dj.get("session", {}).get("ModuleBytes", {}).get("module_bytes", "")
dj["session"]["ModuleBytes"]["module_bytes"] = f"<{len(mb)} chars>"

print(json.dumps(dj, indent=2))

# Check for uppercase hex
full_str = json.dumps(deploy_json)
import re
uppercase_hex = re.findall(r'[A-Fa-f0-9]{20,}', full_str)
for h in uppercase_hex:
    if any(c.isupper() for c in h):
        print(f"\nUPPERCASE HEX FOUND: {h[:60]}...")
