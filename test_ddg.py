import urllib.request
import re

req = urllib.request.Request(
    'https://html.duckduckgo.com/html/?q=cuenca+weather',
    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
)
try:
    html = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
    snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
    for i, s in enumerate(snippets[:3]):
        # strip tags
        clean = re.sub('<[^<]+>', '', s).strip()
        print(f"{i+1}. {clean}")
except Exception as e:
    print(e)
