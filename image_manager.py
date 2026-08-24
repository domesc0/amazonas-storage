import os
import json
import time
import base64
import urllib.request
import urllib.parse
import ssl
import hashlib
import logging
import subprocess
from typing import Optional
from io import BytesIO

# --- 1. FÁJLBA LOGOLÁS BEÁLLÍTÁSA ---
LOG_FILE = "app.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] (IMAGE_MGR): %(message)s",
    encoding="utf-8"
)

IMAGE_MAP_FILE = "image_map.json"
ICON_DIR = os.path.join("assets", "icon")

# Megpróbáljuk betölteni a Pillow-t
try:
    from PIL import Image
    HAS_PIL = True
    logging.info("Pillow (PIL) sikeresen betöltve a képkonvertáláshoz.")
except ImportError:
    HAS_PIL = False
    logging.warning("Pillow (PIL) NEM található! Ajánlott: 'pip install Pillow'")

# --- 2. ÖNGYÓGYÍTÓ ÉS FIXER FUNKCIÓK ---
def self_heal_environment():
    """
    Automatikus hibaelhárító: ha nem léteznek a szükséges mappák
    vagy sérült a JSON registry, automatikusan helyreállítja őket.
    """
    try:
        if not os.path.exists(ICON_DIR):
            os.makedirs(ICON_DIR, exist_ok=True)
            logging.info(f"Auto-Fixer: Mappa létrehozva: {ICON_DIR}")

        if not os.path.exists(IMAGE_MAP_FILE):
            with open(IMAGE_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)
            logging.info(f"Auto-Fixer: Hiányzó {IMAGE_MAP_FILE} létrehozva.")
        else:
            # Érvényesség ellenőrzése
            try:
                with open(IMAGE_MAP_FILE, "r", encoding="utf-8") as f:
                    json.load(f)
            except Exception as e:
                backup_name = f"{IMAGE_MAP_FILE}.corrupted_{int(time.time())}"
                os.rename(IMAGE_MAP_FILE, backup_name)
                with open(IMAGE_MAP_FILE, "w", encoding="utf-8") as f:
                    json.dump({}, f)
                logging.error(f"Auto-Fixer: Sérült {IMAGE_MAP_FILE}! Mentve mint {backup_name}, új JSON generálva.")
    except Exception as e:
        logging.critical(f"Kritikus hiba az környezet helyreállításakor: {e}")

# Futtatás modul betöltésekor
self_heal_environment()


def load_image_map():
    try:
        with open(IMAGE_MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Nem sikerült betölteni a képtérképet: {e}")
        return {}

def save_image_map(image_map_data):
    try:
        with open(IMAGE_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(image_map_data, f, indent=4, ensure_ascii=False)
        logging.debug("Image map sikeresen elmentve.")
    except Exception as e:
        logging.error(f"Képtérkép mentési hiba: {e}")

def extract_direct_image_url(url: str) -> str:
    """ Google Képek átirányító URL kibontása """
    try:
        parsed = urllib.parse.urlparse(url)
        if "google." in parsed.netloc and "/imgres" in parsed.path:
            query = urllib.parse.parse_qs(parsed.query)
            if "imgurl" in query and query["imgurl"]:
                extracted = query["imgurl"][0]
                logging.info(f"Google Képek URL kibontva: {extracted}")
                return extracted
    except Exception as e:
        logging.debug(f"URL kibontási kísérlet sikertelen: {e}")
    return url

# --- 3. REDUNDÁNS LETÖLTŐ MOTOR ---
def fetch_bytes_with_redundancy(url: str) -> Optional[bytes]:
    """
    Többszörös redundanciájú letöltő:
    1. Próba: Standard Python urllib biztonságos fejlécekkel
    2. Próba: Rendszer-szintű cURL hívás (ha az 1. elhasal)
    """
    logging.debug(f"Letöltési kísérlet indítása: {url}")

    # 1. STRATÉGIA: Python urllib
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Referer': 'https://www.google.com/'
            }
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = resp.read()
            if data and len(data) > 100:
                logging.info(f"[STRATÉGIA-1 SIKER] Urllib letöltés kész ({len(data)} bájt)")
                return data
    except Exception as e:
        logging.warning(f"[STRATÉGIA-1 HIBA] Urllib elhasalt: {e}. Váltás cURL-re...")

    # 2. STRATÉGIA: Rendszer curl (Bypassolja a Cloudflare / CDN szigorításokat)
    try:
        cmd = [
            "curl", "-sL", "-k",
            "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "-e", "https://www.google.com/",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        if result.returncode == 0 and len(result.stdout) > 100:
            logging.info(f"[STRATÉGIA-2 SIKER] cURL letöltés kész ({len(result.stdout)} bájt)")
            return result.stdout
        else:
            logging.error(f"[STRATÉGIA-2 HIBA] cURL sikertelen, returncode={result.returncode}")
    except Exception as e:
        logging.error(f"[STRATÉGIA-2 HIBA] cURL futtatási hiba: {e}")

    return None


def process_and_register_image(url_or_file: str, item_id: Optional[int] = None) -> str:
    self_heal_environment()
    clean_val = (url_or_file or "").strip()
    
    if not clean_val:
        return ""

    if not (clean_val.startswith("http://") or clean_val.startswith("https://") or clean_val.startswith("//")):
        return clean_val

    if clean_val.startswith("//"):
        clean_val = "https:" + clean_val

    direct_url = extract_direct_image_url(clean_val)

    # URL tisztítása (ékezetek, szóközök belekódolása)
    parsed_url = urllib.parse.urlparse(direct_url)
    safe_path = urllib.parse.quote(urllib.parse.unquote(parsed_url.path))
    safe_query = urllib.parse.quote(urllib.parse.unquote(parsed_url.query), safe="=&?")
    safe_url = urllib.parse.urlunparse((
        parsed_url.scheme, parsed_url.netloc, safe_path, parsed_url.params, safe_query, parsed_url.fragment
    ))

    img_map = load_image_map()

    # Ellenőrzés: volt-e már letöltve?
    if safe_url in img_map:
        entry = img_map[safe_url]
        local_file = entry.get("local_file", "")
        # Ha a helyi fájl létezik is lemezen
        if local_file and os.path.exists(os.path.join(ICON_DIR, local_file)):
            if item_id and item_id not in entry.get("linked_items", []):
                entry["linked_items"].append(item_id)
                save_image_map(img_map)
            logging.info(f"Kép már létezik a registry-ben: {local_file}")
            return local_file

    # Letöltés futtatása
    raw_bytes = fetch_bytes_with_redundancy(safe_url)
    
    if not raw_bytes:
        logging.error(f"Minden letöltési stratégia csődöt mondott ennél az URL-nél: {safe_url}")
        return safe_url

    url_hash = hashlib.md5(safe_url.encode("utf-8")).hexdigest()[:10]

    # Kép feldolgozása és konvertálása JPG-re (Tablet kompatibilitás)
    if HAS_PIL:
        try:
            img = Image.open(BytesIO(raw_bytes))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            else:
                img = img.convert("RGB")

            filename = f"icon_{url_hash}.jpg"
            filepath = os.path.join(ICON_DIR, filename)
            img.save(filepath, "JPEG", quality=85)

            img_map[safe_url] = {
                "local_file": filename,
                "linked_items": [item_id] if item_id else []
            }
            save_image_map(img_map)
            logging.info(f"Sikeres konverzió és mentés: {filename}")
            return filename
        except Exception as e:
            logging.error(f"Pillow konvertálási hiba: {e}. Mentés nyers fájlként.")

    # Fallback mentés Pillow nélkül
    filename = f"icon_{url_hash}.jpg"
    filepath = os.path.join(ICON_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(raw_bytes)

    img_map[safe_url] = {
        "local_file": filename,
        "linked_items": [item_id] if item_id else []
    }
    save_image_map(img_map)
    return filename


def save_pasted_image(image_data: str, item_id: int) -> str:
    self_heal_environment()
    if not image_data or not image_data.startswith("data:image"):
        logging.error("Érvénytelen Base64 adat érkezett beillesztéskor.")
        raise ValueError("Érvénytelen képformátum")

    header, encoded = image_data.split(",", 1)
    file_bytes = base64.b64decode(encoded)

    if HAS_PIL:
        try:
            img = Image.open(BytesIO(file_bytes))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            else:
                img = img.convert("RGB")

            filename = f"item_{item_id}_{int(time.time())}.jpg"
            filepath = os.path.join(ICON_DIR, filename)
            img.save(filepath, "JPEG", quality=85)
            logging.info(f"Beillesztett kép elmentve JPG-ként: {filename}")
            return filename
        except Exception as e:
            logging.error(f"Beillesztett kép konvertálási hiba: {e}")

    filename = f"item_{item_id}_{int(time.time())}.png"
    filepath = os.path.join(ICON_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    return filename



def sync_all_db_images():
    """
    Keresi az adatbázisban az összes külső (http/https) kép URL-t,
    letölti őket a háttérben, és frissíti a DB rekordokat a helyi fájlnévre.
    """
    import database as db_module
    
    logging.info("[AUTO-SYNC] Adatbázis képszinkronizálás indítása a háttérben...")
    try:
        db = db_module.get_db()
        # Kiszedjük azokat az elemeket, ahol az image_url külső hivatkozás
        rows = db.execute(
            "SELECT id, image_url FROM items WHERE image_url LIKE 'http://%' OR image_url LIKE 'https://%' OR image_url LIKE '//%'"
        ).fetchall()

        if not rows:
            logging.info("[AUTO-SYNC] Nincs letöltendő külső kép az adatbázisban.")
            db.close()
            return

        logging.info(f"[AUTO-SYNC] {len(rows)} db külső kép található, letöltés indul...")

        for item_id, ext_url in rows:
            logging.info(f"[AUTO-SYNC] Elem ID {item_id} képének feldolgozása: {ext_url}")
            local_filename = process_and_register_image(ext_url, item_id)

            # Ha sikerült elmenteni helyi fájlként (és nem a nyers URL-t kaptuk vissza)
            if local_filename and not local_filename.startswith("http"):
                db.execute("UPDATE items SET image_url = ? WHERE id = ?", (local_filename, item_id))
                db.commit()
                logging.info(f"[AUTO-SYNC] DB frissítve! Item {item_id} -> {local_filename}")

        db.close()
        logging.info("[AUTO-SYNC] A képek szinkronizálása sikeresen befejeződött.")
    except Exception as e:
        logging.error(f"[AUTO-SYNC HIBA] Hiba a tömeges letöltés során: {e}")