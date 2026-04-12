"""Quick smoke test for Tavily API connectivity.

Run from the project root:
    python test_tavily.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

key = os.environ.get("TAVILY_API_KEY", "")
if not key:
    print("TAVILY_API_KEY is not set in .env or environment.")
    sys.exit(1)

print(f"Key loaded: {key[:12]}...")

try:
    from tavily import TavilyClient
except ImportError:
    print("tavily-python package not installed. Run: pip install tavily-python")
    sys.exit(1)

client = TavilyClient(api_key=key)

query = "Eastman Chemical price increase 2025"
print(f"\nSearching: {query!r}")

try:
    response = client.search(query=query, max_results=5)
except Exception as e:
    print(f"Tavily API call failed: {e}")
    sys.exit(1)

results = response.get("results", [])
print(f"Results returned: {len(results)}\n")

if not results:
    print("No results — check API key validity and network connectivity.")
    sys.exit(1)

for i, r in enumerate(results, 1):
    print(f"{i}. {r.get('title', 'N/A')}  (score={r.get('score', 0):.2f})")
    print(f"   {r.get('url', '')}")
    print(f"   {r.get('content', '')[:150]}")
    print()

print("Tavily is working.")
