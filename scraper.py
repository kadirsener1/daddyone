#!/usr/bin/env python3
"""
DaddyLive M3U8 Link Extractor
Belirtilen URL'deki iframe içindeki gizli m3u8 yayın linklerini bulur
ve playlist.m3u dosyasına yazar.
"""

import re
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# AYARLAR
# ============================================================
BASE_URL = "https://daddylive.li/embed/embed.php?id=63&player={player_id}&source=tv.json"
PLAYER_RANGE = range(1, 11)  # player=1'den player=10'a kadar
OUTPUT_FILE = "playlist.m3u"
REQUEST_TIMEOUT = 30
PAGE_LOAD_WAIT = 15
MAX_IFRAME_DEPTH = 5  # İç içe iframe derinliği


def create_driver():
    """Headless Chrome WebDriver oluşturur."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--allow-running-insecure-content")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # Tarayıcı loglarını yakalamak için (network isteklerini görmek)
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception:
        # GitHub Actions'da chromium-driver kullanılabilir
        chrome_options.add_argument("--disable-extensions")
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.set_page_load_timeout(REQUEST_TIMEOUT)
    driver.implicitly_wait(10)
    return driver


def extract_m3u8_from_network_logs(driver):
    """Chrome DevTools network loglarından m3u8 linklerini çıkarır."""
    m3u8_links = set()
    try:
        logs = driver.get_log("performance")
        for log_entry in logs:
            try:
                log_data = json.loads(log_entry["message"])
                message = log_data.get("message", {})
                method = message.get("method", "")

                if method in [
                    "Network.requestWillBeSent",
                    "Network.responseReceived",
                    "Network.requestWillBeSentExtraInfo"
                ]:
                    params = message.get("params", {})

                    # requestWillBeSent
                    request_url = params.get("request", {}).get("url", "")
                    if ".m3u8" in request_url:
                        m3u8_links.add(request_url.split("?")[0] if "?" in request_url else request_url)
                        m3u8_links.add(request_url)  # Parametreli halini de ekle

                    # documentURL
                    doc_url = params.get("documentURL", "")
                    if ".m3u8" in doc_url:
                        m3u8_links.add(doc_url)

                    # responseReceived
                    response_url = params.get("response", {}).get("url", "")
                    if ".m3u8" in response_url:
                        m3u8_links.add(response_url)

                    # redirectResponse
                    redirect_url = params.get("redirectResponse", {}).get("url", "")
                    if ".m3u8" in redirect_url:
                        m3u8_links.add(redirect_url)

            except (json.JSONDecodeError, KeyError):
                continue
    except Exception as e:
        logger.warning(f"Network log analizi hatası: {e}")

    return m3u8_links


def extract_m3u8_from_page_source(driver):
    """Sayfa kaynağından m3u8 linklerini regex ile çıkarır."""
    m3u8_links = set()
    try:
        page_source = driver.page_source

        # m3u8 uzantılı URL'leri bul (çeşitli pattern'ler)
        patterns = [
            r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*',
            r'//[^\s\'"<>]+\.m3u8[^\s\'"<>]*',
            r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'src\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'hlsUrl\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'streamUrl\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'video_url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'playlist\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'["\']([^"\']*\.m3u8[^"\']*)["\']',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, page_source, re.IGNORECASE)
            for match in matches:
                url = match.strip()
                if url.startswith("//"):
                    url = "https:" + url
                if ".m3u8" in url:
                    m3u8_links.add(url)

    except Exception as e:
        logger.warning(f"Sayfa kaynağı analizi hatası: {e}")

    return m3u8_links


def extract_m3u8_from_scripts(driver):
    """JavaScript değişkenlerinden m3u8 linklerini çıkarır."""
    m3u8_links = set()

    js_checks = [
        "return typeof hlsUrl !== 'undefined' ? hlsUrl : null;",
        "return typeof source !== 'undefined' ? source : null;",
        "return typeof streamURL !== 'undefined' ? streamURL : null;",
        "return typeof videoUrl !== 'undefined' ? videoUrl : null;",
        "return typeof file !== 'undefined' ? file : null;",
        """
        var results = [];
        var scripts = document.querySelectorAll('script');
        scripts.forEach(function(s) {
            var text = s.textContent || s.innerText || '';
            var matches = text.match(/https?:\\/\\/[^\\s'"<>]+\\.m3u8[^\\s'"<>]*/gi);
            if (matches) results = results.concat(matches);
        });
        return results.length > 0 ? JSON.stringify(results) : null;
        """,
        """
        var videos = document.querySelectorAll('video source, video');
        var urls = [];
        videos.forEach(function(v) {
            if (v.src && v.src.includes('.m3u8')) urls.push(v.src);
            if (v.getAttribute('data-src') && v.getAttribute('data-src').includes('.m3u8'))
                urls.push(v.getAttribute('data-src'));
        });
        return urls.length > 0 ? JSON.stringify(urls) : null;
        """
    ]

    for js in js_checks:
        try:
            result = driver.execute_script(js)
            if result:
                if isinstance(result, str):
                    if result.startswith("["):
                        try:
                            urls = json.loads(result)
                            for url in urls:
                                if ".m3u8" in url:
                                    m3u8_links.add(url)
                        except json.JSONDecodeError:
                            if ".m3u8" in result:
                                m3u8_links.add(result)
                    elif ".m3u8" in result:
                        m3u8_links.add(result)
        except Exception:
            continue

    return m3u8_links


def get_iframe_urls(driver):
    """Sayfadaki tüm iframe URL'lerini bulur."""
    iframe_urls = []
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src")
            if src and src.strip() and src != "about:blank":
                iframe_urls.append(src)

            # data-src attribute'u da kontrol et
            data_src = iframe.get_attribute("data-src")
            if data_src and data_src.strip() and data_src != "about:blank":
                iframe_urls.append(data_src)

    except Exception as e:
        logger.warning(f"İframe URL çıkarma hatası: {e}")

    return iframe_urls


def explore_iframes_recursive(driver, depth=0, visited=None):
    """
    İç içe iframe'leri recursive olarak dolaşır ve m3u8 linklerini toplar.
    """
    if visited is None:
        visited = set()

    if depth > MAX_IFRAME_DEPTH:
        return set()

    all_m3u8 = set()

    # Mevcut frame'deki m3u8 linklerini topla
    all_m3u8.update(extract_m3u8_from_page_source(driver))
    all_m3u8.update(extract_m3u8_from_scripts(driver))

    # İframe'leri bul
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        logger.info(f"{'  ' * depth}Derinlik {depth}: {len(iframes)} iframe bulundu")

        for i, iframe in enumerate(iframes):
            try:
                iframe_src = iframe.get_attribute("src") or f"iframe_{i}"

                if iframe_src in visited:
                    continue
                visited.add(iframe_src)

                logger.info(f"{'  ' * depth}İframe'e geçiliyor [{i}]: {iframe_src[:100]}...")

                # İframe'e geç
                driver.switch_to.frame(iframe)
                time.sleep(2)

                # Bu iframe'deki m3u8'leri topla
                all_m3u8.update(extract_m3u8_from_page_source(driver))
                all_m3u8.update(extract_m3u8_from_scripts(driver))
                all_m3u8.update(extract_m3u8_from_network_logs(driver))

                # Recursive: iç iframe'lere de bak
                all_m3u8.update(explore_iframes_recursive(driver, depth + 1, visited))

                # Ana frame'e geri dön
                driver.switch_to.parent_frame()

            except Exception as e:
                logger.warning(f"{'  ' * depth}İframe [{i}] hatası: {e}")
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    driver.switch_to.default_content()

    except Exception as e:
        logger.warning(f"İframe tarama hatası: {e}")

    return all_m3u8


def process_iframe_urls_directly(driver, iframe_urls, visited=None):
    """
    Bulunan iframe URL'lerini doğrudan ziyaret ederek m3u8 arar.
    Bazı iframe'ler cross-origin olduğu için doğrudan erişim gerekebilir.
    """
    if visited is None:
        visited = set()

    all_m3u8 = set()

    for url in iframe_urls:
        if url in visited:
            continue
        visited.add(url)

        # URL'yi düzelt
        if url.startswith("//"):
            url = "https:" + url

        if not url.startswith("http"):
            continue

        logger.info(f"  İframe URL doğrudan ziyaret ediliyor: {url[:120]}...")

        try:
            driver.get(url)
            time.sleep(PAGE_LOAD_WAIT)

            # m3u8 linklerini topla
            all_m3u8.update(extract_m3u8_from_page_source(driver))
            all_m3u8.update(extract_m3u8_from_scripts(driver))
            all_m3u8.update(extract_m3u8_from_network_logs(driver))

            # Bu sayfadaki iframe'leri de kontrol et (recursive)
            sub_iframe_urls = get_iframe_urls(driver)
            if sub_iframe_urls:
                logger.info(f"    Alt iframe'ler bulundu: {len(sub_iframe_urls)}")
                all_m3u8.update(explore_iframes_recursive(driver, depth=0))

                # Alt iframe URL'lerini de doğrudan ziyaret et
                all_m3u8.update(
                    process_iframe_urls_directly(driver, sub_iframe_urls, visited)
                )

        except Exception as e:
            logger.warning(f"  İframe URL ziyaret hatası: {e}")

    return all_m3u8


def try_requests_method(url):
    """
    requests kütüphanesi ile basit HTTP isteği yaparak m3u8 arar.
    Selenium'a ek olarak alternatif yöntem.
    """
    m3u8_links = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://daddylive.li/",
        "Accept": "*/*",
    }

    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        content = response.text

        # m3u8 linklerini bul
        patterns = [
            r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*',
            r'//[^\s\'"<>]+\.m3u8[^\s\'"<>]*',
            r'["\']([^"\']*\.m3u8[^"\']*)["\']',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                link = match.strip()
                if link.startswith("//"):
                    link = "https:" + link
                if ".m3u8" in link:
                    m3u8_links.add(link)

        # iframe src'lerini de bul ve onları da tara
        soup = BeautifulSoup(content, "html.parser")
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed = urlparse(url)
                    src = f"{parsed.scheme}://{parsed.netloc}{src}"

                if src.startswith("http"):
                    try:
                        resp2 = requests.get(src, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                        for pattern in patterns:
                            matches = re.findall(pattern, resp2.text, re.IGNORECASE)
                            for match in matches:
                                link = match.strip()
                                if link.startswith("//"):
                                    link = "https:" + link
                                if ".m3u8" in link:
                                    m3u8_links.add(link)

                        # İkinci seviye iframe
                        soup2 = BeautifulSoup(resp2.text, "html.parser")
                        for iframe2 in soup2.find_all("iframe"):
                            src2 = iframe2.get("src", "")
                            if src2:
                                if src2.startswith("//"):
                                    src2 = "https:" + src2
                                elif src2.startswith("/"):
                                    parsed2 = urlparse(src)
                                    src2 = f"{parsed2.scheme}://{parsed2.netloc}{src2}"
                                if src2.startswith("http"):
                                    try:
                                        resp3 = requests.get(
                                            src2, headers=headers,
                                            timeout=REQUEST_TIMEOUT, allow_redirects=True
                                        )
                                        for pattern in patterns:
                                            matches = re.findall(pattern, resp3.text, re.IGNORECASE)
                                            for match in matches:
                                                link = match.strip()
                                                if link.startswith("//"):
                                                    link = "https:" + link
                                                if ".m3u8" in link:
                                                    m3u8_links.add(link)
                                    except Exception:
                                        pass
                    except Exception:
                        pass

    except Exception as e:
        logger.warning(f"Requests yöntemi hatası: {e}")

    return m3u8_links


def scrape_player(driver, player_id):
    """Tek bir player ID için m3u8 linklerini toplar."""
    url = BASE_URL.format(player_id=player_id)
    logger.info(f"\n{'='*60}")
    logger.info(f"Player {player_id} taranıyor: {url}")
    logger.info(f"{'='*60}")

    all_m3u8 = set()

    # ---- Yöntem 1: requests ile basit tarama ----
    logger.info("Yöntem 1: requests ile taranıyor...")
    all_m3u8.update(try_requests_method(url))
    if all_m3u8:
        logger.info(f"  requests ile {len(all_m3u8)} m3u8 link bulundu")

    # ---- Yöntem 2: Selenium ile tarama ----
    logger.info("Yöntem 2: Selenium ile taranıyor...")
    try:
        driver.get(url)
        time.sleep(PAGE_LOAD_WAIT)

        # Network loglarından m3u8
        all_m3u8.update(extract_m3u8_from_network_logs(driver))

        # Sayfa kaynağından m3u8
        all_m3u8.update(extract_m3u8_from_page_source(driver))

        # Script değişkenlerinden m3u8
        all_m3u8.update(extract_m3u8_from_scripts(driver))

        # İframe'leri recursive tara
        logger.info("İframe'ler recursive taranıyor...")
        all_m3u8.update(explore_iframes_recursive(driver))

        # İframe URL'lerini topla ve doğrudan ziyaret et
        driver.switch_to.default_content()
        driver.get(url)
        time.sleep(5)

        iframe_urls = get_iframe_urls(driver)
        if iframe_urls:
            logger.info(f"Ana sayfada {len(iframe_urls)} iframe URL bulundu, doğrudan ziyaret ediliyor...")
            all_m3u8.update(process_iframe_urls_directly(driver, iframe_urls))

    except Exception as e:
        logger.error(f"Selenium hatası: {e}")

    # Sonuçları filtrele
    filtered = set()
    for link in all_m3u8:
        # Geçerli m3u8 URL'si mi kontrol et
        if ".m3u8" in link and ("http://" in link or "https://" in link):
            # Temizle
            clean_link = link.strip().strip("'\"")
            filtered.add(clean_link)

    logger.info(f"Player {player_id}: Toplam {len(filtered)} benzersiz m3u8 link bulundu")
    for link in filtered:
        logger.info(f"  → {link[:150]}")

    return filtered


def validate_m3u8_link(url):
    """m3u8 linkinin geçerli olup olmadığını kontrol eder."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://daddylive.li/",
    }
    try:
        response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        return response.status_code == 200
    except Exception:
        try:
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True, stream=True)
            return response.status_code == 200
        except Exception:
            return False


def write_m3u_file(results, output_file):
    """Sonuçları M3U dosyasına yazar."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# DaddyLive M3U8 Playlist\n")
        f.write(f"# Son Güncelleme: {now}\n")
        f.write(f"# Kaynak: daddylive.li - ID:63\n")
        f.write(f"# Toplam Kanal: {sum(len(links) for links in results.values())}\n\n")

        link_counter = 0
        for player_id, links in sorted(results.items()):
            if links:
                for i, link in enumerate(sorted(links), 1):
                    link_counter += 1
                    f.write(f"#EXTINF:-1 group-title=\"Player {player_id}\","
                            f"DaddyLive Player-{player_id} Stream-{i}\n")
                    f.write(f"{link}\n\n")

    logger.info(f"\n{'='*60}")
    logger.info(f"M3U dosyası yazıldı: {output_file}")
    logger.info(f"Toplam {link_counter} stream kaydedildi")
    logger.info(f"{'='*60}")

    return link_counter


def main():
    """Ana fonksiyon."""
    logger.info("DaddyLive M3U8 Extractor başlatılıyor...")
    logger.info(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Taranacak player sayısı: {len(PLAYER_RANGE)}")

    results = {}
    driver = None

    try:
        driver = create_driver()
        logger.info("Chrome WebDriver başarıyla oluşturuldu")

        for player_id in PLAYER_RANGE:
            try:
                links = scrape_player(driver, player_id)
                results[player_id] = links
            except Exception as e:
                logger.error(f"Player {player_id} hatası: {e}")
                results[player_id] = set()

            # Her player arasında kısa bekleme
            time.sleep(3)

    except Exception as e:
        logger.error(f"WebDriver oluşturma hatası: {e}")
        logger.info("Sadece requests yöntemi ile devam ediliyor...")

        for player_id in PLAYER_RANGE:
            url = BASE_URL.format(player_id=player_id)
            logger.info(f"Player {player_id} (requests): {url}")
            links = try_requests_method(url)
            results[player_id] = links
            time.sleep(2)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # M3U dosyasına yaz
    total = write_m3u_file(results, OUTPUT_FILE)

    # Özet
    logger.info("\n" + "=" * 60)
    logger.info("ÖZET RAPOR")
    logger.info("=" * 60)
    for player_id in PLAYER_RANGE:
        count = len(results.get(player_id, set()))
        status = "✅" if count > 0 else "❌"
        logger.info(f"  {status} Player {player_id}: {count} m3u8 link")
    logger.info(f"\n  Toplam: {total} stream")
    logger.info("=" * 60)

    return total


if __name__ == "__main__":
    total = main()
    print(f"\nİşlem tamamlandı. {total} stream bulundu ve playlist.m3u dosyasına yazıldı.")
