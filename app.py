from flask import Flask, render_template, jsonify
import requests
import json
import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from tavily import TavilyClient
from google import genai
import threading

app = Flask(__name__)

# --- CONFIG (Use environment variables for production) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCADrKf3vsWlOYgCw5Kuw0ebpadi-a4h84")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "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")
SEEN_URLS_FILE = "seen_urls.json"

# --- INIT ---
tclient = TavilyClient(TAVILY_API_KEY)
client = genai.Client(api_key=GEMINI_API_KEY)

# Cache for products and gold rate
cache = {
    "gold_rate": 12246.0,
    "tiers": {"tier1": [], "tier2": [], "tier3": []},
    "last_update": None,
    "is_updating": False
}

# === Utility: Load + Save Seen URLs ===
def load_seen_urls():
    if os.path.exists(SEEN_URLS_FILE):
        with open(SEEN_URLS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_seen_urls(data):
    with open(SEEN_URLS_FILE, "w") as f:
        json.dump(data, f)

# === Fetch Accurate Bengaluru Gold Rate ===
def get_gold_rate_bengaluru():
    try:
        resp = requests.get("https://www.goldapi.io/api/XAU/INR", 
                          headers={"x-access-token": "goldapi-h9jxismg9mfhpw-io"}, 
                          timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "price" in data:
                inr_per_gram = data["price"] / 31.1034768
                final_rate = inr_per_gram * 1.09
                return round(final_rate, 2)

        backup = requests.get("https://data-asg.goldprice.org/dbXRates/INR", timeout=10)
        data = backup.json()
        xau_price = data["items"][0]["xauPrice"]
        inr_per_gram = xau_price / 31.1034768
        final_rate = inr_per_gram * 1.09
        return round(final_rate, 2)

    except Exception as e:
        print("Error fetching gold rate:", e)
        return 12246.0

# === Tavily Search ===
def fetch_urls():
    response = tclient.search(query="24k 1 gram gold products", max_results=20, country="india")
    urls = [i["url"] for i in response["results"]]
    return urls

# === Selenium Scraper (Render-compatible) ===
def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    try:
        # For Render deployment
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except:
        # For local development
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    
    return driver

def get_all_visible_text(url, scroll_pause=1, max_scrolls=15):
    driver = get_chrome_driver()
    try:
        driver.get(url)
        last_height = driver.execute_script("return document.body.scrollHeight")

        for _ in range(max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(scroll_pause)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        text = driver.find_element(By.TAG_NAME, "body").text
        return text
    finally:
        driver.quit()

# === Extract product data with Gemini ===
def extract_product_data(url):
    chunk = get_all_visible_text(url)
    prompt = f"""
Extract the following from this chunk of {chunk}:
- product title
- all prices with variants
- keep lowest price in "price"
- url:{url}
Only keep actual 24k 1 gram gold *products* (ignore gold rates, charts, or city-specific pages).
Return JSON:
{{
  "title": "",
  "price": "",
  "url": "",
  "multiple_prices": [{{"variant": "...", "price": "..."}}]
}}
"""
    response = client.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=prompt
    )
    return response.text

# === AI Tiering by Price vs Gold Rate ===
def ai_tier_product(product_json, base_price):
    prompt = f"""
You are a data extraction assistant.
Parse the following JSON list and tier products based on price vs gold rate.

Rules:
- Tier1: 0.1% – 5% lower than gold price
- Tier2: 5.1% – 10% lower
- Tier3: >10.1% lower
Return JSON only:
{{
  "tier1": [...],
  "tier2": [...],
  "tier3": [...]
}}

Products JSON:
{product_json}
Current gold price: {base_price}
"""
    response = client.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=prompt
    )
    return response.text

# === Background Update Function ===
def update_products_background():
    global cache
    cache["is_updating"] = True
    
    try:
        seen_urls = load_seen_urls()
        now = time.time()
        week_sec = 7 * 24 * 60 * 60

        gold_rate = get_gold_rate_bengaluru()
        cache["gold_rate"] = gold_rate

        all_products = []
        urls_tried = 0
        search_rounds = 0
        
        print("🔍 Starting continuous search until products are found in tiers...")
        
        # Keep searching FOREVER until we have products in at least one tier
        while True:
            search_rounds += 1
            print(f"\n🔄 Search Round {search_rounds}")
            
            # Fetch new URLs
            urls = fetch_urls()
            
            # Filter for new or old URLs (reset after a week)
            new_urls = [u for u in urls if u not in seen_urls or now - seen_urls[u] > week_sec]
            
            if not new_urls:
                print("⚠️ No new URLs in this batch. Fetching fresh URLs...")
                # Wait a bit before trying again
                time.sleep(5)
                continue
            
            # Process URLs one by one
            for url in new_urls:
                urls_tried += 1
                print(f"🔍 Scraping URL #{urls_tried}: {url}")
                try:
                    data = extract_product_data(url)
                    
                    # Only add if it looks like valid product data
                    if data and len(data.strip()) > 50:
                        all_products.append(data)
                        seen_urls[url] = now
                        print(f"✅ Product data extracted (Total products: {len(all_products)})")
                    else:
                        print("⚠️ No valid product data found on this page")
                        
                except Exception as e:
                    print(f"❌ Error extracting {url}: {e}")
                    continue
                
                # Try to tier products after every 3 new products
                if len(all_products) >= 3 and len(all_products) % 3 == 0:
                    print(f"\n🏷️ Attempting to tier {len(all_products)} products...")
                    tiers_raw = ai_tier_product(all_products, gold_rate)
                    
                    try:
                        # Clean up markdown code blocks if present
                        tiers_clean = tiers_raw.replace("```json", "").replace("```", "").strip()
                        tiers = json.loads(tiers_clean)
                        
                        # Check if we have products in any tier
                        tier1_count = len(tiers.get("tier1", []))
                        tier2_count = len(tiers.get("tier2", []))
                        tier3_count = len(tiers.get("tier3", []))
                        
                        has_products = tier1_count > 0 or tier2_count > 0 or tier3_count > 0
                        
                        print(f"📊 Tier Results - Tier1: {tier1_count}, Tier2: {tier2_count}, Tier3: {tier3_count}")
                        
                        if has_products:
                            cache["tiers"] = tiers
                            print(f"🎉 SUCCESS! Found products in tiers after trying {urls_tried} URLs!")
                            save_seen_urls(seen_urls)
                            cache["last_update"] = time.time()
                            return  # EXIT - We found products!
                        else:
                            print(f"⏳ No products in tiers yet. Continuing search... (Tried {urls_tried} URLs)")
                            
                    except Exception as e:
                        print(f"❌ Error parsing tiers JSON: {e}")
                        print(f"Raw response: {tiers_raw[:200]}...")
                
                # Small delay between requests to avoid rate limiting
                time.sleep(2)
            
            # After processing all URLs in this batch, continue to next batch
            print(f"📦 Completed batch. Total URLs tried: {urls_tried}. Fetching more URLs...")
            save_seen_urls(seen_urls)
            
    except Exception as e:
        print(f"❌ Critical error in background update: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        cache["is_updating"] = False
        print("🛑 Search stopped.")

# === Flask Routes ===
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/products')
def get_products():
    return jsonify({
        "gold_rate": cache["gold_rate"],
        "tiers": cache["tiers"],
        "last_update": cache["last_update"],
        "is_updating": cache["is_updating"],
        "products_found": {
            "tier1": len(cache["tiers"].get("tier1", [])),
            "tier2": len(cache["tiers"].get("tier2", [])),
            "tier3": len(cache["tiers"].get("tier3", []))
        }
    })

@app.route('/api/refresh')
def refresh_products():
    if not cache["is_updating"]:
        # Clear existing tiers to force new search
        cache["tiers"] = {"tier1": [], "tier2": [], "tier3": []}
        
        thread = threading.Thread(target=update_products_background)
        thread.daemon = True
        thread.start()
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    # Initial data load
    cache["gold_rate"] = get_gold_rate_bengaluru()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
