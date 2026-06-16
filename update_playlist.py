import requests
import re
import json
from datetime import datetime

URL = "https://daddylive.li/embed/embed.php?id=63&player=1&source=tv.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Referer": "https://daddylive.li/",
    "Accept": "application/json, text/*",
}

def get_playlist():
    response = requests.get(URL, headers=HEADERS, timeout=25)
    response.raise_for_status()
    content = response.text

    m3u_lines = ["#EXTM3U"]
    m3u_lines.append(f"# Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    m3u_lines.append(f"# Source: {URL}\n")

    # === JSON Parse Denemesi ===
    try:
        data = json.loads(content)
        
        # DaddyLive JSON yapısı genellikle şu şekildedir:
        channels = data if isinstance(data, list) else data.get('ch', []) or data.get('channels', [])
        
        for ch in channels:
            if isinstance(ch, dict):
                name = ch.get('name') or ch.get('title') or ch.get('channel_name') or "Unknown Channel"
                url = ch.get('url') or ch.get('link') or ch.get('stream')
                
                if url and '.m3u8' in url:
                    m3u_lines.append(f'#EXTINF:-1 group-title="DaddyLive",{name.strip()}')
                    m3u_lines.append(url.strip())
                    m3u_lines.append('')
        print("✅ JSON parse yöntemi ile çekildi.")
        return "\n".join(m3u_lines)
        
    except:
        print("JSON parse edilemedi, regex moduna geçiliyor...")

    # === Regex Yöntemi (Embed içinde gizli linkler için) ===
    m3u8_links = re.findall(r'https?://[^\s\'"<>]+?\.m3u8[^\s\'"<>]*', content)
    
    for i, link in enumerate(m3u8_links, 1):
        m3u_lines.append(f'#EXTINF:-1 tvg-id="dl{i}",DaddyLive {i}')
        m3u_lines.append(link)
        m3u_lines.append('')

    print(f"✅ Regex ile {len(m3u8_links)} adet m3u8 linki bulundu.")
    return "\n".join(m3u_lines)


if __name__ == "__main__":
    playlist = get_playlist()
    
    with open("daddylive.m3u", "w", encoding="utf-8") as f:
        f.write(playlist)
    
    print("🎉 daddylive.mu dosyası başarıyla oluşturuldu!")
