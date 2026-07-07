// Shared key-loading helper for the x402 demo.
//
// Loads the Casper testnet deployer's secp256k1 key from its SEC1/DER PEM file
// into a casper-js-sdk v5 PrivateKey that the casper-x402 library uses directly.

import casperSdk from 'casper-js-sdk';
import fs from 'node:fs';

const { KeyAlgorithm, PrivateKey } = casperSdk;

/**
 * Load a Casper PrivateKey from a PEM file.
 * @param {string} pemPath - path to the PEM file
 * @param {number} [algorithm] - KeyAlgorithm (default SECP256K1)
 * @returns {import('casper-js-sdk').PrivateKey}
 */
export function loadCasperPrivateKey(pemPath, algorithm = KeyAlgorithm.SECP256K1) {
  const pem = fs.readFileSync(pemPath, 'utf8');
  return PrivateKey.fromPem(pem, algorithm);
}

export { casperSdk };
