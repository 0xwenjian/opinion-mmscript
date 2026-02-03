
import os
import socket
import requests
import sys

# From the logs
token_id = "10914690331574804289180861283026548321271353557063641547982208293807941284346"
host = "https://proxy.opinion.trade:8443"
endpoint = f"/openapi/token/orderbook?token_id={token_id}"
url = host + endpoint

print(f"\n[Calculated URL]")
print(url)

print(f"\n[Specific Endpoint Request Test]")
try:
    print(f"Requesting {url}...")
    headers = {
        'User-Agent': 'python-requests/2.32.5', # Mimic standard
        'Accept': 'application/json',
    }
    # Use strict timeout
    resp = requests.get(url, headers=headers, timeout=10)
    print(f"Status Code: {resp.status_code}")
    print(f"Response length: {len(resp.content)}")
    if resp.status_code == 200:
        print("Success! Data received.")
    else:
        print(f"Error response: {resp.text[:200]}")

except Exception as e:
    print(f"Request Failed: {e}")
