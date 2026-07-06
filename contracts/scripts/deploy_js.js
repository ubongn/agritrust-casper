/**
 * Deploy AgriTrust contract to Casper Testnet using casper-js-sdk.
 * This avoids pycspr's secp256k1 signing bugs.
 */
const fs = require('fs');
const path = require('path');
const { 
    CasperClient, 
    CasperServiceByJsonRPC,
    Contracts, 
    Keys, 
    DeployUtil,
    CLValueBuilder,
    CLValueParsers
} = require('casper-js-sdk');

const RPC_URL = 'https://node.testnet.cspr.cloud/rpc';
const AUTH_TOKEN = '55f79117-fc4d-4d60-9956-65423f39a06a';
const CHAIN_NAME = 'casper-test';
const WASM_PATH = path.join(__dirname, '..', 'wasm', 'AgriTrust.wasm');
const PEM_PATH = path.join(__dirname__, '..', 'keys', 'deployer_secret_key.pem');

async function main() {
    // Load key pair from PEM
    const pemContent = fs.readFileSync(PEM_PATH, 'utf-8');
    console.log('PEM loaded');

    // Create SECP256K1 key from PEM
    const keyPair = Keys.Secp256K1.parseKeyPair(pemContent, Keys.SignatureAlgorithm.Secp256K1);
    console.log('Public key:', keyPair.accountHex());

    // Read WASM
    const wasm = new Uint8Array(fs.readFileSync(WASM_PATH));
    console.log(`WASM size: ${wasm.length} bytes`);

    // Create deploy
    const deployParams = new DeployUtil.DeployParams(
        keyPair.publicKey,
        CHAIN_NAME,
        1, // gas_price
        30 * 60 * 1000 // 30 min TTL in ms
    );

    const payment = DeployUtil.standardPayment(800_000_000_000n);

    // Build session args
    const args = DeployUtil.ExecutableDeployItem.newModuleBytes(wasm, DeployUtil.RuntimeArgs.fromMap({
        'odra_cfg_package_hash_key_name': CLValueBuilder.string('agritrust_contract_package_hash'),
        'odra_cfg_allow_key_override': CLValueBuilder.bool(true),
        'odra_cfg_is_upgradable': CLValueBuilder.bool(true),
        'odra_cfg_is_upgrade': CLValueBuilder.bool(false),
    }));

    const deploy = DeployUtil.makeDeploy(deployParams, payment, args);
    console.log('Deploy hash:', DeployUtil.deployHash(deploy).toString('hex'));

    // Sign
    const signedDeploy = DeployUtil.signDeploy(deploy, keyPair);
    console.log('Signed. Approvals:', signedDeploy.approvals.length);

    // Serialize to JSON
    const deployJson = DeployUtil.deployToJson(signedDeploy);
    console.log('JSON keys:', Object.keys(deployJson));
    
    // Submit via custom fetch (with auth header)
    const fetch = (...args) => import('node-fetch').then(({default: fetch}) => fetch(...args));
    
    const response = await fetch(RPC_URL, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${AUTH_TOKEN}`
        },
        body: JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            method: 'account_put_deploy',
            params: { deploy: deployJson }
        })
    });

    const result = await response.json();
    console.log('\nResult:', JSON.stringify(result, null, 2).substring(0, 500));

    if (result.result) {
        const dhash = result.result.deploy_hash;
        console.log(`\nDeploy accepted! Hash: ${dhash}`);
        console.log(`Explorer: https://testnet.cspr.live/deploy/${dhash}`);

        // Wait for processing
        console.log('\nWaiting', process.stdout.write);
        for (let i = 0; i < 36; i++) {
            await new Promise(r => setTimeout(r, 5000));
            try {
                const dr = await fetch(`https://api.testnet.cspr.live/deploys/${dhash}`, {
                    headers: { 'Accept': 'application/json' }
                });
                if (dr.ok) {
                    const data = (await dr.json()).data;
                    if (data && data.status === 'processed') {
                        if (data.error_message) {
                            console.log(`\n\nFAILED: ${data.error_message}`);
                        } else {
                            const cost = parseInt(data.cost) / 1e9;
                            console.log(`\n\n${'='.repeat(60)}`);
                            console.log('SUCCESS! AgriTrust deployed!');
                            console.log(`${'='.repeat(60)}`);
                            console.log(`Deploy: ${dhash}`);
                            console.log(`Cost: ${cost.toFixed(1)} CSPR`);
                        }
                        return;
                    }
                }
            } catch (e) {}
            process.stdout.write('.');
        }
        console.log(' TIMEOUT');
    } else if (result.error) {
        console.log(`\nERROR: ${result.error.message}`);
        if (result.error.data) {
            console.log(`  Detail: ${JSON.stringify(result.error.data).substring(0, 500)}`);
        }
    }
}

main().catch(e => {
    console.error('Fatal:', e.message);
    console.error(e.stack);
    process.exit(1);
});
