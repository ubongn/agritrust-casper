/**
 * AgriTrust x402 Payer Client
 * ============================
 * Pays for data from the x402 data-feed server:
 *
 *   1. GET the resource → receive HTTP 402 + payment challenge.
 *   2. Sign the challenge with the deployer's secp256k1 private key
 *      (genuine ECDSA signature via Node crypto).
 *   3. Re-request with the X-Payment header (base64-encoded signed challenge).
 *   4. Receive the data.
 *
 * Usage:
 *   node client.js fetch <url> [--key path/to/deployer_secret_key.pem]
 *
 * Output: JSON to stdout — { ok: true, data: {...} } or { ok: false, error: "..." }
 */
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";

// ── Parse CLI args ───────────────────────────────────────────────────────────
const { values, positionals } = parseArgs({
  options: {
    key: { type: "string", default: "../keys/deployer_secret_key.pem" },
    max: { type: "string", default: "5000000" },
  },
  allowPositionals: true,
});

const command = positionals[0]; // "fetch"
const url = positionals[1];

if (command !== "fetch" || !url) {
  console.error(JSON.stringify({ ok: false, error: "Usage: node client.js fetch <url> [--key <pem>]" }));
  process.exit(1);
}

// ── Load the deployer's secp256k1 private key ────────────────────────────────
let privateKey;
try {
  const pem = readFileSync(values.key, "utf8");
  // The PEM is an EC PRIVATE KEY — Node crypto can create a key object from it
  privateKey = crypto.createPrivateKey({ key: pem, format: "pem" });
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: `cannot read key file ${values.key}: ${e.message}` }));
  process.exit(0);
}

// ── HTTP helpers (Node 22 has global fetch) ──────────────────────────────────

// Step 1: Request the resource — expect a 402 challenge
try {
  const initialRes = await fetch(url);

  if (initialRes.ok) {
    // Resource is free (shouldn't happen for paywalled endpoints, but handle it)
    const data = await initialRes.json();
    console.log(JSON.stringify({ ok: true, data, payment: null, note: "resource was free" }));
    process.exit(0);
  }

  if (initialRes.status !== 402) {
    const text = await initialRes.text();
    console.log(JSON.stringify({ ok: false, error: `unexpected status ${initialRes.status}: ${text}` }));
    process.exit(0);
  }

  // ── Parse the 402 payment challenge ──
  const challengeBody = await initialRes.json();
  const accept = challengeBody.accepts?.[0];

  if (!accept) {
    console.log(JSON.stringify({ ok: false, error: "no payment requirements in 402 response", body: challengeBody }));
    process.exit(0);
  }

  const { challenge, nonce, expires, amount_motes, payee } = accept;

  if (BigInt(amount_motes) > BigInt(values.max)) {
    console.log(JSON.stringify({ ok: false, error: `price ${amount_motes} motes exceeds max ${values.max}` }));
    process.exit(0);
  }

  console.error(`  [x402] Received 402 challenge: ${amount_motes} motes to ${payee.slice(0, 20)}...`);
  console.error(`  [x402] Nonce: ${nonce} | Expires: ${expires}`);

  // ── Step 2: Sign the challenge with the deployer's secp256k1 key ──
  // This is a REAL ECDSA signature (SHA-256 hash, secp256k1 curve).
  // In production, Casper x402 uses blake2b EIP-712 hashing; the signing
  // mechanism is the same (secp256k1 ECDSA), only the hash function differs.
  const signer = crypto.createSign("SHA256");
  signer.update(challenge);
  signer.end();
  const signature = signer.sign(privateKey).toString("hex");

  console.error(`  [x402] Signed challenge with secp256k1 key: ${signature.slice(0, 32)}...`);

  // ── Step 3: Re-request with the payment header ──
  const paymentHeader = Buffer.from(
    JSON.stringify({
      scheme: "x402",
      resource: new URL(url).pathname,
      nonce: nonce,
      expires: expires,
      amount_motes: amount_motes,
      payee: payee,
      signature: signature,
      signer_pubkey: "0202d328d5aebdfaff2e938bd4ef9edcd8d2c63c8d9fb87d77988f789db9404eb78b",
    })
  ).toString("base64");

  const paidRes = await fetch(url, {
    headers: { "X-Payment": paymentHeader },
  });

  if (!paidRes.ok) {
    const errBody = await paidRes.json().catch(() => ({}));
    console.log(JSON.stringify({ ok: false, error: `payment rejected (${paidRes.status})`, detail: errBody }));
    process.exit(0);
  }

  // ── Step 4: Data released! ──
  const result = await paidRes.json();

  console.error(`  [x402] ✅ Payment settled — data released`);
  console.log(JSON.stringify({ ok: true, data: result.data, payment: { amount_motes, nonce, settled: true } }));
  process.exit(0);

} catch (e) {
  console.log(JSON.stringify({ ok: false, error: e.message }));
  process.exit(0);
}
