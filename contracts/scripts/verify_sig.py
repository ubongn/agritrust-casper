"""Verify pycspr's signature locally before submitting."""
import json
import hashlib
import pycspr
from pycspr import create_deploy_parameters, create_standard_payment, create_deploy, read_wasm
from pycspr.factory import create_private_key, create_public_key
from pycspr.crypto import get_key_pair_from_pem_file, KeyAlgorithm
from pycspr.types import ModuleBytes, DeployArgument, CL_String, CL_Bool
from pathlib import Path
from ecdsa import VerifyingKey, SigningKey, SECP256k1
from ecdsa.util import sigdecode_string

KEY_PEM = str(Path(__file__).parent.parent / "keys" / "deployer_secret_key.pem")
WASM_PATH = str(Path(__file__).parent.parent / "wasm" / "AgriTrust.wasm")

algo = KeyAlgorithm.SECP256K1
pvk_bytes, pbk_bytes = get_key_pair_from_pem_file(KEY_PEM, algo)
pvk = create_private_key(algo, pvk_bytes, pbk_bytes)
pbk = create_public_key(algo, pbk_bytes)

print(f"Private key ({len(pvk_bytes)} bytes): {pvk_bytes.hex()}")
print(f"Public key ({len(pbk_bytes)} bytes): {pbk_bytes.hex()}")

# Create deploy
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
dhash = deploy.hash
print(f"\nDeploy hash: {dhash.hex()}")
print(f"Hash length: {len(dhash)} bytes")

deploy.approve(pvk)
print(f"\nApproval count: {len(deploy.approvals)}")
for ap in deploy.approvals:
    print(f"Signer: {ap.signer.algo} {ap.signer.pbk.hex()}")
    print(f"Signature ({len(ap.signature)} bytes): {ap.signature.hex()}")

    # Verify signature using ecdsa library
    # The signature in pycspr for secp256k1 is 65 bytes: recovery_id(1) + r(32) + s(32)
    sig = ap.signature
    if len(sig) == 65:
        recovery_id = sig[0]
        r = int.from_bytes(sig[1:33], 'big')
        s = int.from_bytes(sig[33:65], 'big')
        print(f"Recovery ID: {recovery_id}")
        print(f"r: {hex(r)[:20]}...")
        print(f"s: {hex(s)[:20]}...")

        # Verify using the public key
        # For secp256k1, the compressed public key starts with 02 or 03
        compressed_prefix = pbk_bytes[0]
        x = int.from_bytes(pbk_bytes[1:33], 'big')
        print(f"Compressed prefix: {compressed_prefix}")
        print(f"x coord: {hex(x)[:20]}...")

        # Try to decompress the public key
        # y^2 = x^3 + 7 mod p
        p = SECP256k1.order  # This is the curve order, not the field prime
        # Actually we need the field prime
        from ecdsa.curves import SECP256k1 as curve_info
        field_prime = curve_info.curve.p()
        y_squared = (pow(x, 3, field_prime) + 7) % field_prime
        y = pow(y_squared, (field_prime + 1) // 4, field_prime)
        if (y % 2) != (compressed_prefix - 2):
            y = field_prime - y

        vk = VerifyingKey.from_string(
            x.to_bytes(32, 'big') + y.to_bytes(32, 'big'),
            curve=SECP256k1
        )

        # Verify: signature is r || s (without recovery byte)
        rs_sig = sig[1:]  # r || s
        try:
            valid = vk.verify(rs_sig, dhash, hashfunc=hashlib.sha256, sigdecode=sigdecode_string)
            print(f"\nSignature valid: {valid}")
        except Exception as e:
            print(f"\nSignature verification failed: {e}")

        # Also try with blake2b hash (Casper uses blake2b)
        try:
            valid2 = vk.verify(rs_sig, dhash, hashfunc=hashlib.blake2b, sigdecode=sigdecode_string)
            print(f"Signature valid (blake2b): {valid2}")
        except Exception as e:
            print(f"Signature verification failed (blake2b): {e}")
