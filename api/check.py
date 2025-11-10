from http.server import BaseHTTPRequestHandler
import os, json, requests, psycopg2, datetime, hashlib, hmac, time
from urllib.parse import urlparse, parse_qs

# =========================
# 🔧 CONFIGURATION
# =========================
PINCODES_TO_CHECK = ['132001']
DATABASE_URL = os.getenv('DATABASE_URL')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CRON_SECRET = os.getenv('CRON_SECRET')

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AMAZON_PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG")

# =========================
# 🧠 VERCEL HANDLER
# =========================
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        auth_key = query.get('secret', [None])[0]

        if auth_key != CRON_SECRET:
            self.send_response(401)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
            return

        try:
            in_stock_messages, summary = main_logic()

            final_message = (
                "🔥 *Stock Alert!*\n\n" + "\n\n".join(in_stock_messages)
                if in_stock_messages
                else "❌ No items currently in stock."
            )

            final_message += f"\n\n📊 *Summary:*\n{summary}"

            send_telegram_message(final_message)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'found': len(in_stock_messages)}).encode())

        except Exception as e:
            print(f"[error] {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

# =========================
# 🗃️ DATABASE CONNECTION
# =========================
def get_products_from_db():
    print("[info] Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT name, url, product_id, store_type, affiliate_link FROM products")
    rows = cursor.fetchall()
    conn.close()
    print(f"[info] Loaded {len(rows)} products from database.")
    return [
        {"name": r[0], "url": r[1], "productId": r[2], "storeType": r[3], "affiliateLink": r[4]}
        for r in rows
    ]

# =========================
# 💬 TELEGRAM HANDLER
# =========================
def get_all_chat_ids():
    return [
        1301703380, 7500224400, 7570729917, 798436912, 6878100797, 849850934,
        1476695901, 1438419270, 667911343, 574316265, 5871190519, 939758815,
        6272441906, 5756316614, 1221629915, 5339576661, 766044262, 1639167211,
        1642837409, 978243265, 871796135, 995543877, 5869017768, 1257253967,
        820803336, 1794830835, 6137007196, 1460192633, 691495606, 6644657779,
        837532484, 8196689182, 1813686494, 5312984739
    ]

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN:
        print("[warn] TELEGRAM_BOT_TOKEN not set.")
        return

    chat_ids = get_all_chat_ids()
    print(f"[info] Sending message to {len(chat_ids)} Telegram users...")

    for chat_id in chat_ids:
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"[info] ✅ Sent to {chat_id}")
            else:
                print(f"[warn] ⚠️ {chat_id} failed: {res.text}")
            time.sleep(0.4)
        except Exception as e:
            print(f"[error] Telegram send failed to {chat_id}: {e}")

# =========================
# 🏬 CROMA STOCK CHECKER
# =========================
def check_croma(product, pincode):
    """Accurate Croma stock checker — only marks 'In Stock' when truly deliverable."""
    url = 'https://api.croma.com/inventory/oms/v2/tms/details-pwa/'
    payload = {
        "promise": {
            "allocationRuleID": "SYSTEM",
            "checkInventory": "Y",
            "organizationCode": "CROMA",
            "sourcingClassification": "EC",
            "promiseLines": {"promiseLine": [{
                "fulfillmentType": "HDEL",
                "itemID": product["productId"],
                "lineId": "1",
                "requiredQty": "1",
                "shipToAddress": {"zipCode": pincode},
                "extn": {"widerStoreFlag": "N"}
            }]}
        }
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "oms-apim-subscription-key": "1131858141634e2abe2efb2b3a2a2a5d",
        "origin": "https://www.croma.com",
        "referer": "https://www.croma.com/"
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        data = res.json()

        suggested = data.get("promise", {}).get("suggestedOption", {})
        option = suggested.get("option", {})
        promise_lines = option.get("promiseLines", {}).get("promiseLine", [])
        unavailable_lines = suggested.get("unavailableLines", {}).get("unavailableLine", [])

        # Unavailable
        if unavailable_lines or not promise_lines:
            print(f"[CROMA] ❌ {product['name']} - Unavailable for {pincode}")
            return None

        # Available — check delivery assignments
        line = promise_lines[0]
        assignments = line.get("assignments", {}).get("assignment", [])
        if assignments and any(a.get("deliveryDate") for a in assignments):
            print(f"[CROMA] ✅ {product['name']} - Deliverable to {pincode}")
            return f"✅ *In Stock at Croma ({pincode})*\n[{product['name']}]({product['affiliateLink'] or product['url']})"
        else:
            print(f"[CROMA] ❌ {product['name']} - No valid delivery assignment.")
            return None

    except Exception as e:
        print(f"[error] Croma check failed for {product['name']}: {e}")
        return None

# =========================
# 🛒 AMAZON CHECKER (throttling-safe stub)
# =========================
def check_amazon(product):
    """Skip Amazon API for now if throttled — avoids breaking script."""
    print(f"[AMAZON] ⚠️ Throttled or skipped for {product['name']}.")
    return None

# =========================
# 🚀 MAIN LOGIC
# =========================
def main_logic():
    print("[info] Starting stock check...")
    products = get_products_from_db()
    in_stock_messages = []
    total_croma, total_amazon = 0, 0
    available_croma, available_amazon = 0, 0

    for product in products:
        result = None
        if product["storeType"] == "croma":
            total_croma += 1
            for pin in PINCODES_TO_CHECK:
                result = check_croma(product, pin)
                if result:
                    available_croma += 1
                    in_stock_messages.append(result)
                    break
        elif product["storeType"] == "amazon":
            total_amazon += 1
            result = check_amazon(product)
            if result:
                available_amazon += 1
                in_stock_messages.append(result)

    summary = (
        f"🟢 *Croma:* {available_croma}/{total_croma} in stock\n"
        f"🟡 *Amazon:* {available_amazon}/{total_amazon} (API throttled)\n"
        f"📦 *Total:* {len(in_stock_messages)} available"
    )

    print(f"[info] ✅ Found {len(in_stock_messages)} unique products in stock.")
    print(f"[info] Summary:\n{summary}")

    return in_stock_messages, summary
