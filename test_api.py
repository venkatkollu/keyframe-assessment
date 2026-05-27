import time
import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

def test_api():
    print("=== Testing Health Check ===")
    r = requests.get(f"{BASE_URL}/health")
    print(f"Health check status: {r.status_code}")
    print(r.json())
    assert r.status_code == 200

    print("\n=== Testing Authentication Failure (Missing Key) ===")
    r = requests.post(f"{BASE_URL}/v1/transcribe", files={"file": ("dummy.mp4", b"data")})
    print(f"Status code: {r.status_code}")
    print(json.dumps(r.json(), indent=2))
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "MISSING_API_KEY"

    print("\n=== Testing Authentication Failure (Invalid Key) ===")
    r = requests.post(
        f"{BASE_URL}/v1/transcribe",
        headers={"X-API-Key": "sk_invalid"},
        files={"file": ("dummy.mp4", b"data")}
    )
    print(f"Status code: {r.status_code}")
    print(json.dumps(r.json(), indent=2))
    assert r.status_code == 401
    assert r.json()["detail"]["error"]["code"] == "INVALID_API_KEY"

    print("\n=== Creating API Key ===")
    r = requests.post(
        f"{BASE_URL}/v1/keys",
        headers={"X-Admin-Token": "admin_secret_token_123"},
        json={"owner": "TestRunner", "rate_limit_rpm": 3, "quota_limit_usd": 5.0}
    )
    print(f"Status code: {r.status_code}")
    key_info = r.json()
    print(json.dumps(key_info, indent=2))
    assert r.status_code == 200
    api_key = key_info["key"]

    print("\n=== Testing Rate Limiting (Limit = 3 RPM) ===")
    headers = {"X-API-Key": api_key}
    responses = []
    # Send 5 requests quickly
    for i in range(5):
        r = requests.post(
            f"{BASE_URL}/v1/transcribe",
            headers=headers,
            data={"url_req_str": json.dumps({"url": "https://example.com/dummy.mp4"})}
        )
        responses.append(r)
        print(f"Request {i+1} status: {r.status_code}")
        if r.status_code == 429:
            print("Rate limit hit successfully!")
            print(json.dumps(r.json(), indent=2))
            break
        time.sleep(0.1)
    
    assert any(res.status_code == 429 for res in responses), "Rate limit 429 was not triggered"

    print("\n=== Creating a Second API Key for Transcription (Limit = 60 RPM) ===")
    r = requests.post(
        f"{BASE_URL}/v1/keys",
        headers={"X-Admin-Token": "admin_secret_token_123"},
        json={"owner": "TestRunner2", "rate_limit_rpm": 60, "quota_limit_usd": 10.0}
    )
    print(f"Status code: {r.status_code}")
    key_info2 = r.json()
    assert r.status_code == 200
    api_key2 = key_info2["key"]
    headers2 = {"X-API-Key": api_key2}

    print("\n=== Submitting Transcription Job (via Public Video URL) ===")
    # Use a small test video link
    test_video_url = "https://www.w3schools.com/html/mov_bbb.mp4"
    r = requests.post(
        f"{BASE_URL}/v1/transcribe",
        headers=headers2,
        data={"url_req_str": json.dumps({"url": test_video_url})}
    )
    print(f"Status code: {r.status_code}")
    job_info = r.json()
    print(json.dumps(job_info, indent=2))
    assert r.status_code == 202
    job_id = job_info["job_id"]

    print("\n=== Polling Job Status ===")
    completed = False
    for attempt in range(20):
        print(f"Poll attempt {attempt+1}/20...")
        r = requests.get(f"{BASE_URL}/v1/jobs/{job_id}", headers=headers2)
        status_info = r.json()
        print(f"Job status: {status_info.get('status')}")
        if status_info.get("status") in ("completed", "failed"):
            print("Job finished!")
            print(json.dumps(status_info, indent=2))
            completed = True
            break
        time.sleep(5)
    
    assert completed, "Job did not finish processing in time"

    print("\n=== Querying Usage & Quotas ===")
    r = requests.get(f"{BASE_URL}/v1/usage", headers=headers2)
    print(f"Usage response status: {r.status_code}")
    print(json.dumps(r.json(), indent=2))
    assert r.status_code == 200

    print("\n=== Testing llms.txt endpoint ===")
    r = requests.get(f"{BASE_URL}/llms.txt")
    print(f"llms.txt status: {r.status_code}")
    print(r.text[:200] + "...")
    assert r.status_code == 200

    print("\nALL API TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    try:
        test_api()
    except Exception as e:
        print(f"\nTEST RUN FAILED: {e}")
        sys.exit(1)
