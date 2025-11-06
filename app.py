import os
import threading
import time
import requests
from flask import Flask, jsonify, render_template
from tavily import TavilyClient

# Flask setup
app = Flask(__name__)

# Tavily setup
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "tvly-xxxxxx")  # Replace with your key
tavily = TavilyClient(api_key=TAVILY_API_KEY)

# Cached data
cached_data = {
    "gold_rate": None,
    "deals": []
}


def fetch_gold_rate():
    """Fetches the current gold rate in INR using GoldAPI"""
    url = "https://www.goldapi.io/api/XAU/INR"
    headers = {"x-access-token": os.getenv("GOLD_API_KEY", "goldapi-xxxxxx")}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        rate = data.get("price_gram_24k")
        return rate
    except Exception as e:
        print(f"Error fetching gold rate: {e}")
        return None


def fetch_gold_deals():
    """Fetches gold-related deals using Tavily API"""
    try:
        # Removed 'country' argument, now part of query text
        response = tavily.search("best gold jewellery deals India", max_results=5)
        deals = []
        for item in response.get("results", []):
            deals.append({
                "title": item.get("title"),
                "url": item.get("url")
            })
        return deals
    except Exception as e:
        print(f"Error fetching gold deals: {e}")
        return []


def background_updater():
    """Background task to refresh gold rate & deals every minute"""
    while True:
        try:
            cached_data["gold_rate"] = fetch_gold_rate()
            cached_data["deals"] = fetch_gold_deals()
            print("✅ Updated gold data successfully.")
        except Exception as e:
            print(f"Error in background update: {e}")
        time.sleep(60)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/products")
def api_products():
    """Return cached deals"""
    return jsonify(cached_data["deals"] or [])


@app.route("/api/refresh")
def api_refresh():
    """Manually refresh gold rate and deals"""
    cached_data["gold_rate"] = fetch_gold_rate()
    cached_data["deals"] = fetch_gold_deals()
    return jsonify({"status": "ok"})


@app.route("/api/goldrate")
def api_gold_rate():
    """Return current gold rate"""
    return jsonify({
        "gold_rate": cached_data["gold_rate"]
    })


if __name__ == "__main__":
    # Start background thread
    threading.Thread(target=background_updater, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

