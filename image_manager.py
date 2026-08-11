import os
import json
import time
import base64
import urllib.request
import hashlib
from typing import Optional

IMAGE_MAP_FILE = "image_map.json"
ICON_DIR = os.path.join("assets", "icon")

os.makedirs(ICON_DIR, exist_ok=True)

def load_image_map():
    if not os.path.exists(IMAGE_MAP_FILE):
        return {}
    try:
        with open(IMAGE_MAP_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_image_map(image_map_data):
    with open(IMAGE_MAP_FILE, "w") as f:
        json.dump(image_map_data, f, indent=4)

def process_and_register_image(url_or_file: str, item_id: Optional[int] = None) -> str:
    """
    Keresi a képet a registry-ben. Ha új URL, letölti. Ha létezik, hozzácsatolja az elemhez.
    """
    clean_val = (url_or_file or "").strip()
    if not clean_val:
        return ""

    if clean_val.startswith("http://") or clean_val.startswith("https://"):
        img_map = load_image_map()

        # Ha már letöltöttük korábban: nem tölti le újra, csak regisztrálja az item_id-t
        if clean_val in img_map:
            entry = img_map[clean_val]
            if item_id and item_id not in entry.get("linked_items", []):
                entry["linked_items"].append(item_id)
                save_image_map(img_map)
            return entry["local_file"]

        # Új URL letöltése
        try:
            url_hash = hashlib.md5(clean_val.encode("utf-8")).hexdigest()[:10]
            ext = os.path.splitext(clean_val.split("?")[0])[1].lower()
            if ext not in [".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"]:
                ext = ".png"

            filename = f"icon_{url_hash}{ext}"
            filepath = os.path.join(ICON_DIR, filename)

            req = urllib.request.Request(clean_val, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response, open(filepath, 'wb') as out_file:
                out_file.write(response.read())

            img_map[clean_val] = {
                "local_file": filename,
                "linked_items": [item_id] if item_id else []
            }
            save_image_map(img_map)
            return filename
        except Exception as e:
            print(f"Hiba a kép letöltése során ({clean_val}): {e}")
            return clean_val

    return clean_val

def save_pasted_image(image_data: str, item_id: int) -> str:
    """
    Bemásolt Base64 kép mentése a lemezre.
    """
    if not image_data or not image_data.startswith("data:image"):
        raise ValueError("Érvénytelen képformátum")

    header, encoded = image_data.split(",", 1)
    ext = "png"
    if "jpeg" in header or "jpg" in header:
        ext = "jpg"
    elif "webp" in header:
        ext = "webp"

    file_bytes = base64.b64decode(encoded)
    filename = f"item_{item_id}_{int(time.time())}.{ext}"
    filepath = os.path.join(ICON_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(file_bytes)

    return filename