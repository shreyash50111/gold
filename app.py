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

# === Dynamic Query Generator using Gemini ===
def generate_dynamic_queries(base_price):
    """Generate additional search queries on-the-fly using Gemini"""
    prompt = f"""
Generate 20 diverse search queries for finding 24k 1 gram gold products online in India.

Current market rate: ₹{base_price} per gram

Requirements for queries:
- Must include price constraints like "under ₹{int(base_price)}", "below {int(base_price)} rupees", "less than ₹{int(base_price)}"
- Focus on products CHEAPER than market rate
- Include Indian shopping terms and contexts
- Mix of brand names, product types, and price-focused searches
- Include regional variations (Hindi/English mix acceptable)
- Must be realistic queries someone would search on Google/shopping sites

Return ONLY a JSON array of strings (no other text):
["query 1", "query 2", ...]

Example format:
["24k gold coin 1 gram under ₹{int(base_price)}", "cheap 1g pure gold below market price"]
"""
    
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt
        )
        
        # Parse the response
        query_text = response.text.strip()
        # Remove markdown code blocks if present
        query_text = query_text.replace("```json", "").replace("```", "").strip()
        queries = json.loads(query_text)
        
        if isinstance(queries, list) and len(queries) > 0:
            print(f"   🤖 Generated {len(queries)} dynamic queries using AI")
            return queries
        else:
            print("   ⚠️ Invalid query format from Gemini")
            return []
    except Exception as e:
        print(f"   ⚠️ Error generating dynamic queries: {e}")
        return []

# === Tavily Search with Dynamic Queries ===
def fetch_urls(search_round=0, gold_rate=12246.0):
    # Base queries with price constraints
    base_queries = [
        # Price-focused searches
        f"24k 1 gram gold under ₹{int(gold_rate)} India",
        f"1 gram gold coin below {int(gold_rate)} rupees",
        f"24 karat 1g gold less than ₹{int(gold_rate)}",
        f"cheap 1 gram pure gold under {int(gold_rate)} INR",
        f"discounted 24k gold 1g below market price",
        f"1 gram gold bar under ₹{int(gold_rate)} online",
        f"affordable 24 carat 1g gold below {int(gold_rate)}",
        
        # Direct product searches with rupees
        "24k 1 gram gold products India rupees price",
        "buy 1 gram gold coin 24 karat online price INR",
        "24k gold 1g bar price India rupees buy",
        "pure gold 1 gram jewelry online INR shopping",
        "24 carat gold 1 gram buy online India price list",
        "1 gram 999 purity gold product rupees online",
        "24k hallmarked gold 1 gram INR purchase",
        "fine gold 1g coin purchase India online shopping",
        "sovereign gold 1 gram online buy india",
        "gold biscuit 1 gram 24k buy India price",
        
        # Investment focused
        "certified 24 karat 1 gram gold investment online",
        "investment gold 1g bar India rupees buy",
        "24k gold wafer 1 gram online price shopping",
        "pure gold pendant 1 gram 24k INR buy",
        "gold chip 1 gram 999 purity buy online",
        "digital gold 1 gram 24k purchase India",
        "gold savings 1 gram pure online buy",
        "small gold investment 1g India online",
        
        # Brand/retailer specific
        "tanishiq 1 gram gold coin 24k price online",
        "malabar gold 1 gram 24 karat price buy",
        "kalyan jewellers 1g gold product online",
        "joyalukkas 1 gram pure gold buy online",
        "PC jeweller 1g 24k gold online shopping",
        "senco gold 1 gram coin price buy",
        "reliance jewels 1g gold 24k online",
        "amazon 1 gram gold coin 24k India buy",
        "flipkart gold 1g 24 karat shopping",
        "paytm 1 gram gold 24k buy online",
        "phonepe gold 1g 24 karat purchase",
        
        # Specific product types
        "24k gold bar 1 gram India online buy",
        "gold coin 1g 999 purity buy shopping",
        "lakshmi gold coin 1 gram 24k online",
        "ganesh gold coin 1g pure buy India",
        "gold round 1 gram 24 karat online",
        "gold nugget 1g pure online shopping",
        "gold ingot 1 gram 24k India buy",
        "gold bullion 1g 999 purity online",
        "gold chip 1 gram investment buy",
        
        # Price comparison searches
        "cheapest 1 gram gold 24k India online",
        "best price 1g gold coin online buy",
        "lowest rate 24k gold 1 gram India",
        "discount 1 gram pure gold buy online",
        "offer 24 karat gold 1g India shopping",
        "sale 1 gram gold product online buy",
        "deal 24k gold 1g India online",
        "promo 1 gram pure gold buy",
        
        # City specific
        "bengaluru 1 gram gold 24k online buy",
        "bangalore gold 1g pure buy shopping",
        "mumbai 1 gram 24k gold price online",
        "delhi 1g gold coin online buy",
        "chennai 24 karat 1 gram gold shopping",
        "hyderabad 1g pure gold buy online",
        "pune 1 gram gold 24k online",
        "kolkata 24 karat 1g gold buy",
        
        # Quality focused with price
        "BIS hallmark 1 gram gold 24k price",
        "certified pure gold 1g India buy",
        "authentic 24k gold 1 gram buy online",
        "genuine 1g gold 999 purity shopping",
        "guaranteed 24 karat gold 1 gram buy",
        "verified gold 1g pure online purchase",
        
        # Additional variations
        "one gram gold 24 carat India buy",
        "1gm 24kt gold product online shopping",
        "single gram pure gold buy online",
        "mini gold bar 1g 24k buy",
        "small gold coin 1 gram India online",
        "pocket gold 1g 999 purity buy",
        "affordable 24k gold 1 gram shopping",
        "gold gifting 1g pure online buy",
        
        # E-commerce focused
        "1 gram gold online shopping India",
        "buy 24k gold 1g delivery India",
        "gold 1 gram home delivery online",
        "1g pure gold cash on delivery",
        "24 karat 1 gram gold free shipping",
        
        # Occasion based
        "wedding gold coin 1 gram 24k",
        "diwali gold 1g 24 karat online",
        "gift gold coin 1 gram pure",
        "festival gold 1g buy online India"
    ]
    
    # Every 10 rounds, generate fresh queries using Gemini
    if search_round > 0 and search_round % 10 == 0:
        print(f"\n🤖 Generating dynamic queries using AI (Round {search_round})...")
        dynamic_queries = generate_dynamic_queries(gold_rate)
        if dynamic_queries:
            # Add dynamic queries to the pool
            all_queries = base_queries + dynamic_queries
        else:
            all_queries = base_queries
    else:
        all_queries = base_queries
    
    # Select query based on search round
    query = all_queries[search_round % len(all_queries)]
    print(f"🔍 Search Query #{search_round + 1}: '{query}'")
    
    try:
        response = tclient.search(query=query, max_results=25, country="india")
        urls = [i["url"] for i in response["results"]]
        print(f"   📥 Received {len(urls)} URLs from search")
        return urls
    except Exception as e:
        print(f"   ❌ Error in Tavily search: {e}")
        return []

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
Extract the following from this webpage content:
- product title
- all prices with variants (MUST be in Indian Rupees ₹ or INR)
- keep lowest price in "price" field
- url: {url}

IMPORTANT: 
- Only extract actual 24k or 999 purity 1 gram gold *products* (ignore gold rates, charts, or city-specific rate pages)
- Prices MUST be in Indian Rupees (₹/INR)
- Include the ₹ symbol or "INR" in the price field
- If no valid products found, return empty JSON

Return JSON:
{{
  "title": "product name",
  "price": "₹X,XXX or INR X,XXX",
  "url": "{url}",
  "multiple_prices": [{{"variant": "...", "price": "₹..."}}]
}}

Webpage content:
{chunk[:5000]}
"""
    response = client.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=prompt
    )
    return response.text

# === AI Tiering by Price vs Gold Rate ===
def ai_tier_product(product_json, base_price):
    prompt = f"""
You are a data extraction assistant for gold product pricing.

CRITICAL INSTRUCTIONS:
1. All prices MUST be in Indian Rupees (₹/INR)
2. Parse prices carefully - extract numeric values from formats like "₹5,240" or "INR 5240"
3. Compare each product's price against the current gold rate: ₹{base_price} per gram

Tiering Rules (based on percentage below market rate):
- Tier1: 0.1% – 5% lower than ₹{base_price}
- Tier2: 5.1% – 10% lower than ₹{base_price}
- Tier3: More than 10.1% lower than ₹{base_price}

Products that are equal to or MORE expensive than ₹{base_price} should be EXCLUDED from all tiers.

Return JSON only (no markdown):
{{
  "tier1": [products with prices 0.1%-5% below ₹{base_price}],
  "tier2": [products with prices 5.1%-10% below ₹{base_price}],
  "tier3": [products with prices >10.1% below ₹{base_price}]
}}

Products to analyze:
{product_json}

Current 24K gold market rate in Bengaluru: ₹{base_price} per gram
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
        consecutive_no_new_urls = 0
        
        print("🔍 Starting continuous search until products are found in tiers...")
        
        # Keep searching FOREVER until we have products in at least one tier
        while True:
            search_rounds += 1
            print(f"\n🔄 Search Round {search_rounds}")
            
            # Fetch new URLs with rotating queries
            urls = fetch_urls(search_rounds)
            
            if not urls:
                print("⚠️ No URLs returned from search. Retrying...")
                time.sleep(5)
                continue
            
            # Filter for new or old URLs (reset after a week)
            new_urls = [u for u in urls if u not in seen_urls or now - seen_urls[u] > week_sec]
            
            if not new_urls:
                consecutive_no_new_urls += 1
                print(f"⚠️ No new URLs in this batch. Consecutive strikes: {consecutive_no_new_urls}")
                
                # If we've hit too many consecutive rounds with no new URLs, reset the seen_urls partially
                if consecutive_no_new_urls >= 3:
                    print("🔄 Resetting old URL cache to get fresh results...")
                    # Keep only URLs from the last hour
                    hour_ago = now - 3600
                    seen_urls = {url: timestamp for url, timestamp in seen_urls.items() if timestamp > hour_ago}
                    consecutive_no_new_urls = 0
                    save_seen_urls(seen_urls)
                
                # Wait before trying with a different query
                time.sleep(5)
                continue
            else:
                consecutive_no_new_urls = 0  # Reset counter when we find new URLs
            
            print(f"📦 Found {len(new_urls)} new URLs to process")
            
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
            
            # After processing all URLs in this batch, save progress and continue
            print(f"📦 Completed batch. Total URLs tried: {urls_tried}. Rotating to next query...")
            save_seen_urls(seen_urls)
            
            # Small delay before next search round
            time.sleep(3)
            
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
