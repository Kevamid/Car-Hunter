import re
from datetime import datetime, timedelta

def sanitize_title(raw_title: str) -> str:
    """Strips 'SPONSORED', 'FEATURED', and trailing price tags from card headings."""
    if not raw_title:
        return ""
    
    # 1. Strip 'SPONSORED' or 'FEATURED' prefix (case-insensitive)
    cleaned = re.sub(r'^(sponsored|featured)\b\s*', '', raw_title, flags=re.IGNORECASE)
    
    # 2. Remove price appended at the end of the title string (e.g., £15,995 or £15995)
    cleaned = re.sub(r'£\d{1,3}(,\d{3})*\s*$', '', cleaned)
    
    # 3. Normalize multiple whitespace spaces
    return re.sub(r'\s+', ' ', cleaned).strip()


def parse_date_listed(date_text: str) -> bool:
    """
    Evaluates whether the listing was posted within the allowed window (e.g. 24–48 hours).
    Returns True if listing is fresh, False if stale.
    """
    if not date_text:
        return True  # If no date string is found, let it pass to rely on DB duplication checks
        
    text_lower = date_text.lower().strip()
    
    # Instant matches for fresh listings
    if any(keyword in text_lower for keyword in ["just added", "today", "hour", "minute"]):
        return True
    if "yesterday" in text_lower:
        return True
        
    # Check for 'X days ago' pattern
    days_match = re.search(r'(\d+)\s*day', text_lower)
    if days_match:
        days_old = int(days_match.group(1))
        return days_old <= 2  # Keep listings <= 2 days old (48h max)
        
    # If explicit date string like '15 Oct 2026'
    try:
        parsed_date = datetime.strptime(date_text, "%d %b %Y")
        return (datetime.now() - parsed_date) <= timedelta(hours=48)
    except ValueError:
        pass
        
    return True


def get_pistonheads_listings(page, base_url: str):
    """Fetches PistonHeads search page, ensuring explicit DateCreatedDesc sort."""
    
    # Force 'Most Recent' sort order via query parameter if missing
    if "sortOptions=" not in base_url and "sort=" not in base_url:
        delimiter = "&" if "?" in base_url else "?"
        target_url = f"{base_url}{delimiter}sortOptions=DateCreatedDesc"
    else:
        target_url = base_url

    page.goto(target_url, wait_until="domcontentloaded")
    
    listing_cards = page.query_selector_all(".ad-listing, [data-ad-id]")
    extracted_data = []

    for card in listing_cards:
        # Extract title element
        title_el = card.query_selector(".ad-heading, .listing-title, h3")
        raw_title = title_el.inner_text() if title_el else ""
        clean_title = sanitize_title(raw_title)

        # Extract date added / age tag
        date_el = card.query_selector(".ad-date, .listing-posted-date, .date-added")
        date_text = date_el.inner_text() if date_el else ""

        # Check age filter (Skip stale/old ads)
        if not parse_date_listed(date_text):
            continue

        # Extract URL & Price
        link_el = card.query_selector("a[href*='/buy/listing/']")
        url = link_el.get_attribute("href") if link_el else ""
        if url and not url.startswith("http"):
            url = f"https://www.pistonheads.com{url}"

        price_el = card.query_selector(".ad-price, .price")
        price = price_el.inner_text().strip() if price_el else "N/A"

        if clean_title and url:
            extracted_data.append({
                "title": clean_title,
                "price": price,
                "url": url,
                "date_text": date_text
            })

    return extracted_data