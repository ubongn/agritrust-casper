"""Integration test for the AgriTrust API backend."""
import requests, time, sys, threading
import uvicorn

# Start uvicorn in background thread
config = uvicorn.Config('api.main:app', host='127.0.0.1', port=8855, log_level='error')
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True)
t.start()
time.sleep(3)

base = 'http://127.0.0.1:8855'
passed = 0
failed = 0

def test(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}  {detail}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")

# 1. Health
r = requests.get(f'{base}/api/health')
test("health", r.status_code == 200, str(r.json()))

# 2. Stats
r = requests.get(f'{base}/api/stats')
test("stats", r.status_code == 200 and 'total_invoices' in r.json())

# 3. Chain status
r = requests.get(f'{base}/api/chain/status')
cs = r.json()
test("chain status", cs.get('connected') == True, f"height={cs.get('block_height')}")

# 4. List invoices
r = requests.get(f'{base}/api/invoices')
data = r.json()
test("list invoices", data['count'] >= 1, f"count={data['count']}")

# 5. Get invoice detail
r = requests.get(f'{base}/api/invoice/1')
inv = r.json()
test("invoice #1 settled", inv['status'] == 3, f"status={inv['status_label']}")
test("invoice #1 has verdict", inv.get('verdict') is not None)

# 6. Register new invoice
r = requests.post(f'{base}/api/invoice/register', json={
    'commodity': 'cocoa', 'region': 'Abuja, Nigeria',
    'face_amount_cspr': 750, 'maturity_days': 90
})
result = r.json()
test("register invoice", result.get('ok') == True, f"new id={result['invoice']['id']}")
new_id = result['invoice']['id']

# 7. Evaluate (AI underwriting)
r = requests.post(f'{base}/api/invoice/{new_id}/evaluate')
result = r.json()
test("evaluate (AI verdict)", result.get('ok') == True,
     f"band={result['verdict']['risk_band']} score={result['verdict']['score']}")
test("evaluate score in range", 0 <= result['verdict']['score'] <= 1000)
test("evaluate has data_hash", len(result['verdict']['data_hash']) > 5)

# 8. Fund (LP)
r = requests.post(f'{base}/api/invoice/{new_id}/fund')
result = r.json()
test("fund invoice", result.get('ok') == True, result.get('message', ''))

# 9. Settle
r = requests.post(f'{base}/api/invoice/{new_id}/settle')
result = r.json()
test("settle invoice", result.get('ok') == True, result.get('message', ''))

# 10. Final stats increased
r = requests.get(f'{base}/api/stats')
stats = r.json()
test("stats increased", stats['total_invoices'] >= 2,
     f"invoices={stats['total_invoices']} funded={stats['total_funded_cspr']}CSPR")

# 11. Transactions
r = requests.get(f'{base}/api/transactions?limit=10')
txs = r.json()['transactions']
test("transactions logged", len(txs) >= 5, f"count={len(txs)}")

# 12. Full lifecycle verified
r = requests.get(f'{base}/api/invoice/{new_id}')
inv = r.json()
test("full lifecycle complete", inv['status'] == 3,
     f"REGISTERED→EVALUATED→FUNDED→SETTLED [{inv['status_label']}]")

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("ALL TESTS PASSED")
sys.exit(0 if failed == 0 else 1)
