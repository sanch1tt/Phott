import http.client, json, time, re, uuid
from jwt import decode
from fastapi import FastAPI, Request
from TempMail import TempMail
from urllib.parse import urlparse

app = FastAPI()

# ========= HTTPS Helpers =========

def https_post(host, url, data, headers):
    conn = http.client.HTTPSConnection(host)
    json_data = json.dumps(data)
    conn.request("POST", url, body=json_data, headers=headers)
    response = conn.getresponse()
    return json.loads(response.read())

def https_get(host, url, headers):
    conn = http.client.HTTPSConnection(host)
    conn.request("GET", url, headers=headers)
    response = conn.getresponse()
    return json.loads(response.read())

def approve_link(link):
    try:
        parsed = urlparse(link)
        conn = http.client.HTTPSConnection(parsed.hostname)
        conn.request("GET", parsed.path + "?" + parsed.query, headers={
            "User-Agent": "Mozilla/5.0"
        })
        conn.getresponse().read()
    except Exception as e:
        print("approve_link error:", e)

# ========= Magic Link & Token =========

def send_magiclink(email, device_id):
    try:
        return https_post("prodapi.phot.ai", "/app/api/v1/magiclink/phot", {
            "baseUrl": "https://www.phot.ai",
            "email": email,
            "redirectPage": "",
            "packageId": "PACKAGE_ID_PHOT_AI_WEB",
            "country": "India",
            "countryCode": "IN",
            "newsletter": False,
            "deviceId": device_id
        }, {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }).get("tokenId")
    except Exception as e:
        print("send_magiclink error:", e)
        return None

def extract_link(text):
    m = re.search(r'https://[^"\s>]+magiclink-verify/phot/approve\?tokenId=[\w\-]+', text)
    return m.group(0) if m else None

def poll_token(token_id):
    try:
        return https_get("prodapi.phot.ai", f"/app/api/v1/magiclink-verify/phot/poll?tokenId={token_id}", {
            "User-Agent": "Mozilla/5.0"
        })
    except Exception as e:
        print("poll_token error:", e)
        return None

def get_access_token():
    tmail = TempMail()
    inbox = tmail.createInbox()
    device_id = str(uuid.uuid4())
    token_id = send_magiclink(inbox.address, device_id)
    if not token_id:
        return None

    print("[*] Waiting for magic link email...")
    for _ in range(24):
        emails = tmail.getEmails(inbox.token)
        if emails:
            body = emails[0].body if emails[0].body != "TEXT_FORMAT_BODY" else (emails[0].html or "")
            link = extract_link(body)
            if link:
                approve_link(link)
                data = poll_token(token_id)
                if data and "accessToken" in data:
                    payload = decode(data["accessToken"], options={"verify_signature": False})
                    return {
                        "token": data["accessToken"],
                        "deviceId": payload["deviceId"],
                        "workspace": list(payload["teams"])[0]
                    }
        time.sleep(5)
    return None

def make_headers(token_data):
    return {
        "authorization": f"Bearer {token_data['token']}",
        "content-type": "application/json",
        "origin": "https://studio.phot.ai",
        "referer": "https://studio.phot.ai/",
        "user-agent": "Mozilla/5.0",
        "x-user-country": "IN",
        "x-user-current-workspace": token_data["workspace"],
        "x-user-device-id": token_data["deviceId"],
        "x-user-ip": "27.60.15.17",
        "x-user-platform": "STUDIO"
    }

# ========= FastAPI Routes =========

@app.get("/")
def root():
    return {"status": "Phot AI FastAPI server is running"}

@app.get("/gen")
def generate(prompt: str = ""):
    if not prompt:
        return {"error": "Missing prompt"}

    token_data = get_access_token()
    if not token_data:
        return {"error": "Token generation failed"}

    headers = make_headers(token_data)
    payload = {
        "prompt": prompt,
        "guidance_scale": 7.5,
        "image_strength": 1,
        "negative_prompt": "",
        "num_outputs": 4,
        "aspect_ratio": "1:1",
        "studio_options": {"style": {"style": []}},
        "input_image_link": ""
    }

    try:
        res = https_post("prodapi.phot.ai", "/v5/create-art", payload, headers)
        if "data" not in res or "order_id" not in res["data"]:
            return {"error": "Invalid or expired token"}
        oid = res["data"]["order_id"]
        print("[*] Order started:", oid)
    except Exception as e:
        return {"error": f"create-art failed: {str(e)}"}

    for i in range(20):
        try:
            check = https_get("prodapi.phot.ai", f"/app/api/v2/user_activity/order-status?order_id={oid}", headers)
            if check.get("order_status_code") == 200:
                return {
                    "status": "complete",
                    "prompt": prompt,
                    "urls": [i["url"] for i in check["output_urls"]],
                    "used_by": token_data["deviceId"]
                }
        except Exception as e:
            print("Polling error:", e)
        time.sleep(1)

    return {"error": "Image generation timed out"}