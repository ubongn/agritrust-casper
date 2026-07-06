const fs = require('fs');
const { DeployUtil, Keys, CLValueBuilder } = require('casper-js-sdk');

const RPC_URL = 'https://node.testnet.cspr.cloud/rpc';
const AUTH_TOKEN = '55f79117-fc4d-4d60-9956-65423f39a06a';
const CHAIN_NAME = 'casper-test';
const WASM_PATH = 'C:/Users/Sabiedu/.qwenpaw/workspaces/hack_1/agritrust-casper/contracts/wasm/AgriTrust.wasm';

// Private key PEM
const PEM_PATH = 'C:/Users/Sabiedu/.qwenpaw/workspaces/hack_1/agritrust-casper/contracts/keys/deployer_secret_key.pem';

async function main() {
    // Create key pair from PEM file
    const keyPair = Keys.Secp256K1.loadKeyPairFromPrivateFile(PEM_PATH);
    console.log('Public key:', keyPair.accountHex());

    const wasm = new Uint8Array(fs.readFileSync(WASM_PATH));
    console.log('WASM size:', wasm.length, 'bytes');

    const deployParams = new DeployUtil.DeployParams(
        keyPair.publicKey,
        CHAIN_NAME,
        1,
        DeployUtil.DEFAULT_DEPLOY_TTL
    );

    const payment = DeployUtil.standardPayment(800_000_000_000n);

    const session = DeployUtil.ExecutableDeployItem.newModuleBytes(wasm, DeployUtil.RuntimeArgs.fromMap({
        'odra_cfg_package_hash_key_name': CLValueBuilder.string('agritrust_contract_package_hash'),
        'odra_cfg_allow_key_override': CLValueBuilder.bool(true),
        'odra_cfg_is_upgradable': CLValueBuilder.bool(true),
        'odra_cfg_is_upgrade': CLValueBuilder.bool(false),
    }));

    const deploy = DeployUtil.makeDeploy(deployParams, payment, session);
    const hash = DeployUtil.deployHash(deploy);
    console.log('Deploy hash:', Buffer.from(hash).toString('hex'));

    const signedDeploy = DeployUtil.signDeploy(deploy, keyPair);
    console.log('Signed. Approvals:', signedDeploy.approvals.length);
    console.log('Signer:', signedDeploy.approvals[0].signer);
    console.log('Signature (first 40):', signedDeploy.approvals[0].signature.substring(0, 40) + '...');

    const deployJson = DeployUtil.deployToJson(signedDeploy);
    
    // Show session type to verify structure
    console.log('Session type:', Object.keys(deployJson.session));

    const response = await fetch(RPC_URL, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + AUTH_TOKEN
        },
        body: JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            method: 'account_put_deploy',
            params: { deploy: deployJson }
        })
    });

    const result = await response.json();
    console.log('\nResult:', JSON.stringify(result, null, 2).substring(0, 600));

    if (result.result) {
        const dhash = result.result.deploy_hash;
        console.log('\nDeploy accepted! Hash:', dhash);
        console.log('Explorer: https://testnet.cspr.live/deploy/' + dhash);

        process.stdout.write('\nWaiting');
        for (let i = 0; i < 36; i++) {
            await new Promise(r => setTimeout(r, 5000));
            try {
                const dr = await fetch('https://api.testnet.cspr.live/deploys/' + dhash, {
                    headers: { 'Accept': 'application/json' }
                });
                if (dr.ok) {
                    const body = await dr.json();
                    const data = body.data;
                    if (data && data.status === 'processed') {
                        if (data.error_message) {
                            console.log('\n\nFAILED:', data.error_message);
                        } else {
                            const cost = parseInt(data.cost) / 1e9;
                            console.log('\n\n============================================================');
                            console.log('SUCCESS! AgriTrust deployed!');
                            console.log('============================================================');
                            console.log('Deploy:', dhash);
                            console.log('Cost:', cost.toFixed(1), 'CSPR');
                        }
                        process.exit(0);
                    }
                }
            } catch (e) {}
            process.stdout.write('.');
        }
        console.log(' TIMEOUT');
    } else if (result.error) {
        console.log('\nERROR:', result.error.message);
        if (result.error.data) {
            console.log('  Detail:', JSON.stringify(result.error.data).substring(0, 500));
        }
    }
}

main().catch(e => {
    console.error('Fatal:', e.message);
    console.error(e.stack);
    process.exit(1);
});
