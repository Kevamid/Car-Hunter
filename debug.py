from curl_cffi import requests

# Use a session object to automatically store and send cookies
session = requests.Session()

# 1. Warm-up GET request to establish session cookies and bypass checks
print("[+] Visiting search page to establish session...")
get_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
res_get = session.get(
    "https://www.pistonheads.com/buy/search?make=bmw&model=m140i&priceTo=22000", 
    headers=get_headers, 
    impersonate="chrome"
)
print(f"GET Status Code: {res_get.status_code}")

# 2. Now send the GraphQL POST request using the warmed session
post_headers = {
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://www.pistonheads.com",
    "Referer": "https://www.pistonheads.com/buy/search?make=bmw&model=m140i&priceTo=22000",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}

payload = {
    "operationName": "GetClassifiedsSearch",
    "variables": {
        "filters": {
            "make": "bmw",
            "model": "m140i",
            "priceTo": 22000
        },
        "page": 1,
        "pageSize": 30
    },
    "query": """
    query GetClassifiedsSearch($filters: SearchFiltersInput, $page: Int, $pageSize: Int) {
      searchClassifieds(filters: $filters, page: $page, pageSize: $pageSize) {
        totalResults
        results {
          id
          headline
          price
        }
      }
    }
    """
}

print("[+] Sending GraphQL POST request...")
res_post = session.post(
    "https://www.pistonheads.com/api/graphql", 
    headers=post_headers, 
    json=payload, 
    impersonate="chrome"
)

print(f"POST Status Code: {res_post.status_code}")
print("POST Response Body:")
print(res_post.text[:1000])  # Print first 1000 chars of response