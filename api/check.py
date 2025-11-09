from http.server import BaseHTTPRequestHandler
import os, json, requests, psycopg2
from urllib.parse import urlparse, parse_qs

# --- 1. CONFIGURATION ---
PINCODES_TO_CHECK = ['132001']
DATABASE_URL = os.environ.get('DATABASE_URL')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CRON_SECRET = os.environ.get('CRON_SECRET')

# --- 2. VERCEK HANDLER ---
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        auth_key = query_components.get('secret', [None])[0]

        if auth_key != CRON_SECRET:
            self.send_response(401) # Unauthorized
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Unauthorized. Check your CRON_SECRET.'}).encode())
            return

        try:
            in_stock_messages = main_logic()
            
            if in_stock_messages:
                print(f"Found {len(in_stock_messages)} items in stock. Sending Telegram message.")
                final_message = "🔥 *Stock Alert!*\n\n" + "\n\n".join(in_stock_messages)
                send_telegram_message(final_message)
            else:
                print("All items out of stock. No message sent.")

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'found': len(in_stock_messages)}).encode())
            
        except Exception as e:
            print(f"An error occurred: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

# --- 3. DATABASE: FETCH PRODUCTS ---
def get_products_from_db():
    print("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT name, url, product_id, store_type, affiliate_link FROM products")
    products = cursor.fetchall()
    conn.close()
    
    products_list = [
        {"name": row[0], "url": row[1], "productId": row[2], "storeType": row[3], "affiliateLink": row[4]}
        for row in products
    ]
    print(f"Found {len(products_list)} products in the database.")
    return products_list

# --- 4. TELEGRAM SENDER ---
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram BOT TOKEN not set. Skipping message.")
        return
    chat_ids = ['7992845749', '984016385' , '6644657779' , '8240484793' , '1813686494' ,'1438419270' ,'939758815' , '7500224400' , '8284863866' , '837532484' , '667911343' , '1476695901' , '6878100797' , '574316265' , '1460192633' , '978243265' ,'5871190519' ,'766044262' ,'1639167211' , '849850934' ,'757029917' , '5756316614' ,'5339576661' , '6137007196' , '7570729917']
    print(f"Sending message to {len(chat_ids)} users...")
    for chat_id in chat_ids:
        if not chat_id.strip(): continue
        url = f"https{api.telegram.org/bot}{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': chat_id.strip(), 'text': message, 'parse_mode': 'Markdown', 'disable_web_page_preview': True}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Failed to send message to {chat_id}: {e}")

# --- 5. CROMA CHECKER (UPDATED FOR DEBUGGING) ---
def check_croma(product, pincode):
    url = 'https://api.croma.com/inventory/oms/v2/tms/details-pwa/'
    payload = {"promise": {"allocationRuleID": "SYSTEM", "checkInventory": "Y", "organizationCode": "CROMA", "sourcingClassification": "EC", "promiseLines": {"promiseLine": [{"fulfillmentType": "HDEL", "itemID": product["productId"], "lineId": "1", "requiredQty": "1", "shipToAddress": {"zipCode": pincode}, "extn": {"widerStoreFlag": "N"}}]}}}
    headers = {'accept': 'application/json', 'content-type': 'application/json', 'oms-apim-subscription-key': '1131858141634e2abe2efb2b3a2a2a5d', 'origin': 'https://www.croma.com', 'referer': 'https://www.croma.com/'}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        res.raise_for_status() 
        
        # --- THIS IS THE NEW DEBUGGING LOGIC ---
        try:
            # Try to parse JSON normally
            data = res.json()
            # If it works, check for stock
            if data.get("promise", {}).get("suggestedOption", {}).get("option", {}).get("promiseLines", {}).get("promiseLine"):
                link_to_send = product["affiliateLink"] or product["url"]
                return { "name": product["name"], "url": link_to_send }
        except requests.exceptions.JSONDecodeError:
            # This is the "Expecting value" error.
            # If it fails, print the raw text response to the logs.
            print("--- CROMA DEBUG: FAILED TO PARSE JSON ---")
            print(f"Product: {product['name']}")
            print(f"Status Code: {res.status_code}")
            print(f"Raw Response Text: {res.text}")
            print("--- END CROMA DEBUG ---")
        # ----------------------------------------
            
    except Exception as e:
        print(f'Error checking Croma ({product["name"]}): {e}')
    return None

# --- 6. FLIPKART CHECKER ---
# (We are still skipping this, but the function is here if you want to add a proxy later)
def check_flipkart(product, pincode):
    print(f'Skipping Flipkart check for {product["name"]}.')
    return None
    
# --- 7. MAIN LOGIC ---
def main_logic():
    print("Starting stock check...")
    try:
        products_to_track = get_products_from_db()
    except Exception as e:
        print(f"Failed to fetch products from database: {e}")
        send_telegram_message(f"❌ Your checker script failed to connect to the database.")
        return [] 

    in_stock_messages = []
    
    for product in products_to_track:
        for pincode in PINCODES_TO_CHECK:
            result_data = None
            if product["storeType"] == 'croma':
                result_data = check_croma(product, pincode)
            elif product["storeType"] == 'flipkart':
                result_data = check_flipkart(product, pincode) # This will just skip
            
            if result_data:
                message = f'✅ *In Stock at {product["storeType"].capitalize()} ({pincode})*\n[{result_data["name"]}]({result_data["url"]})'
                in_stock_messages.append(message)
    
    return in_stock_messages