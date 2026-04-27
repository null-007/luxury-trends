from playwright.sync_api import sync_playwright
import time
import re
from database import setup_database, upsert_listing, get_stats, save_trending_term, get_weekly_trends
from dashboard import generate_dashboard

QUERIES = [
    "designer jacket",
    "luxury jacket",
    "designer bag",
    "luxury handbag",
    "designer shoes",
    "luxury sneakers",
    "designer dress",
    "luxury coat",
    "designer top",
    "luxury boots",
    "designer belt",
    "luxury jewelry",
    "designer sunglasses",
    "luxury watch",
    "designer scarf",
]

def scrape_query(page, query):
    url = f"https://www.depop.com/search/?q={query.replace(' ', '+')}&price_min=150&price_max=500&sort=priceDescending"
    page.goto(url)
    page.wait_for_timeout(3000)
    
    prices = page.query_selector_all('[aria-label="Price"]')
    products = []
    
    for price in prices:
        card = price.evaluate_handle("el => el.closest('li')")
        card_el = card.as_element()
        
        if card_el is None:
            continue
        
        lines = card_el.inner_text().strip().split("\n")
        lines = [l.strip() for l in lines if l.strip()]
        
        brand = lines[0] if len(lines) > 0 else "N/A"
        size  = lines[1] if len(lines) > 1 else "N/A"
        price = lines[2] if len(lines) > 2 else "N/A"
        
        img = card_el.query_selector("img")
        raw_url = img.get_attribute("src") if img else ""
        img_url = raw_url.replace("/P10.jpg", "/P8.jpg") if raw_url else ""
        
        link = card_el.query_selector("a")
        href = link.get_attribute("href") if link else ""
        full_link = f"https://www.depop.com{href}" if href else ""
        
        match = re.search(r'/products/([^/]+)/', href)
        item_id = match.group(1) if match else href
        
        if item_id:
            products.append({
                "id": item_id,
                "brand": brand,
                "size": size,
                "price": price,
                "query": query,
                "image": img_url,
                "link": full_link,
            })
    
    print(f"'{query}': {len(products)} products found")
    return products

def mark_sold_listings_verified(seen_ids, page):
    import sqlite3
    from datetime import datetime
    
    conn = sqlite3.connect("depop.db")
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute("""
        SELECT id, link FROM listings 
        WHERE status = 'available' AND last_seen < ?
    """, (today,))
    
    missing = cursor.fetchall()
    sold_count = 0
    not_sold_count = 0
    
    print(f"Checking {len(missing)} missing listings...")
    
    for (listing_id, link) in missing:
        if listing_id in seen_ids:
            continue
        
        try:
            page.goto(link, timeout=10000)
            page.wait_for_timeout(800)
            
            content = page.content()
            
            if "SoldOut" in content:
                cursor.execute("UPDATE listings SET status = 'sold' WHERE id = ?", (listing_id,))
                cursor.execute("""
                    INSERT INTO daily_snapshots (listing_id, date, price, status)
                    VALUES (?, ?, '', 'sold')
                """, (listing_id, today))
                sold_count += 1
            else:
                not_sold_count += 1
                
        except Exception as e:
            print(f"Error checking {link}: {e}")
            continue
        
        time.sleep(0.5)
    
    conn.commit()
    conn.close()
    print(f"Verified {sold_count} sold, {not_sold_count} still available")

def get_trending_searches(page):
    try:
        page.goto("https://www.depop.com/")
        page.wait_for_timeout(2000)
        
        page.click('#searchBar__input', timeout=5000)
        page.wait_for_timeout(2000)
        
        trending = page.query_selector_all('#trending-searches li')
        terms = []
        for item in trending:
            text = item.inner_text().strip().lower()
            if text:
                terms.append(text)
        
        print(f"Found {len(terms)} trending searches: {terms}")
        return terms
    except Exception as e:
        print(f"Could not fetch trending searches: {e}")
        return []

def main():
    setup_database()
    all_products = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        trending_terms = get_trending_searches(page)
        
        luxury_keywords = ["jacket", "bag", "designer", "luxury", "vintage", "coat", 
                          "shoes", "boots", "watch", "jewelry", "scarf", "dress",
                          "lululemon", "arcteryx", "moncler", "canada goose", "supreme",
                          "stone island", "off white", "gucci", "prada", "louis vuitton",
                          "chanel", "burberry", "balenciaga", "bottega", "acne"]
        
        all_queries = list(QUERIES)
        for term in trending_terms:
            if term not in all_queries:
                if any(keyword in term.lower() for keyword in luxury_keywords):
                    all_queries.append(term)
                    print(f"Added luxury trending query: {term}")
                else:
                    print(f"Skipped non-luxury trending query: {term}")
        
        for query in all_queries:
            products = scrape_query(page, query)
            all_products.extend(products)
            
            if query in trending_terms:
                save_trending_term(query, len(products))
            
            time.sleep(2)
        
        seen_ids = set()
        for product in all_products:
            upsert_listing(product)
            seen_ids.add(product["id"])
        
        mark_sold_listings_verified(seen_ids, page)
        
        browser.close()
    
    stats = get_stats()
    generate_dashboard(stats, all_queries)

main()