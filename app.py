import os
import json
import math
from typing import Optional
from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import threading
import database as db_module
import image_manager as img_mgr

app = FastAPI(title="AMAZONAS STORAGE SYSTEM")
app.add_middleware(SessionMiddleware, secret_key="amazonas_secret_key_123")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("assets/icon", exist_ok=True)
os.makedirs("assets/storage", exist_ok=True)
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

templates = Jinja2Templates(directory="templates")
CONFIG_FILE = "config.json"


def load_config():
    default_config = {"mode": "inventory", "items_per_page": 20}
    if not os.path.exists(CONFIG_FILE):
        save_config(default_config)
        return default_config
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            if "items_per_page" not in cfg:
                cfg["items_per_page"] = 20
            return cfg
    except Exception:
        return default_config

def save_config(config_data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

def init_db():
    db = db_module.get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY,
            type_code TEXT NOT NULL,
            main_num INTEGER NOT NULL,
            sub_num INTEGER,
            description TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER,
            FOREIGN KEY (parent_id) REFERENCES categories (id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            quantity INTEGER DEFAULT 1,
            container_id TEXT NOT NULL,
            category_id INTEGER,
            image_url TEXT,
            datasheet_url TEXT,
            FOREIGN KEY (container_id) REFERENCES containers (id),
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
    """)
    
    for col in ["category_id INTEGER", "image_url TEXT", "datasheet_url TEXT"]:
        try:
            db.execute(f"ALTER TABLE items ADD COLUMN {col}")
        except Exception:
            pass
            
    db.commit()
    db.close()

init_db()

threading.Thread(target=img_mgr.sync_all_db_images, daemon=True).start()

def flash(request: Request, message: str, category: str = "success"):
    if "flash_messages" not in request.session:
        request.session["flash_messages"] = []
    request.session["flash_messages"].append({"message": message, "category": category})

def get_flashed_messages(request: Request):
    return request.session.pop("flash_messages", [])

templates.env.globals["get_flashed_messages"] = get_flashed_messages


# --- ROUTES ---

@app.get("/toggle-mode")
async def toggle_mode(request: Request):
    config = load_config()
    config["mode"] = "edit" if config.get("mode") == "inventory" else "inventory"
    save_config(config)
    flash(request, f"Mode switched: {config['mode'].upper()}", "info")
    return RedirectResponse(url="/", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, q: Optional[str] = None, page: int = Query(1, ge=1)):
    search_query = (q or "").strip()
    config = load_config()
    current_mode = config.get("mode", "inventory")
    
    items_per_page = 20 if search_query else (3 if current_mode == "edit" else 20)

    db = db_module.get_db()
    base_query = """
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        LEFT JOIN categories p ON c.parent_id = p.id
    """
    
    where_clause = ""
    params = []
    
    if search_query:
        where_clause = """ WHERE i.name LIKE ? OR i.description LIKE ? OR i.container_id LIKE ? 
                           OR c.name LIKE ? OR p.name LIKE ? """
        params = [f"%{search_query}%"] * 5

    total_found = db.execute(f"SELECT COUNT(*) {base_query} {where_clause}", params).fetchone()[0]
    total_pages = max(1, math.ceil(total_found / items_per_page))
    
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * items_per_page

    select_query = f"""
        SELECT i.*, c.name as category_name, p.name as parent_category_name 
        {base_query} {where_clause}
        ORDER BY i.id DESC
        LIMIT ? OFFSET ?
    """
    
    items = db.execute(select_query, params + [items_per_page, offset]).fetchall()
    containers = db.execute("SELECT * FROM containers ORDER BY type_code ASC, main_num ASC, sub_num ASC").fetchall()
    subcategories = db.execute("""
        SELECT c.id, c.name, p.name as parent_name 
        FROM categories c
        JOIN categories p ON c.parent_id = p.id
        ORDER BY p.name ASC, c.name ASC
    """).fetchall()

    total_items = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    total_containers = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0]
    db.close()

    start_p = max(1, page - 2)
    end_p = min(total_pages, page + 2)
    page_range = list(range(start_p, end_p + 1))

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "items": items, 
            "containers": containers, 
            "subcategories": subcategories,
            "search_query": search_query,
            "total_items": total_items,
            "total_containers": total_containers,
            "mode": current_mode,
            "current_page": page,
            "total_pages": total_pages,
            "page_range": page_range
        }
    )


@app.post("/upload-pasted-icon/{item_id}")
async def upload_pasted_icon(item_id: int, request: Request):
    try:
        data = await request.json()
        filename = img_mgr.save_pasted_image(data.get("image"), item_id)

        db = db_module.get_db()
        db.execute("UPDATE items SET image_url = ? WHERE id = ?", (filename, item_id))
        db.commit()
        db.close()

        flash(request, f"Image uploaded: {filename}", "success")
        return JSONResponse({"success": True, "filename": filename})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/add-item")
async def add_item(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    quantity: int = Form(1),
    container_id: str = Form(...),
    category_id: Optional[str] = Form(None),
    image_url: Optional[str] = Form("")
):
    name_clean = name.strip()
    desc_clean = description.strip()
    cat_val = int(category_id) if category_id and category_id.isdigit() else None
    
    if name_clean and container_id:
        db = db_module.get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO items (name, description, quantity, container_id, category_id, image_url) VALUES (?, ?, ?, ?, ?, ?)",
            (name_clean, desc_clean, quantity, container_id, cat_val, "")
        )
        new_item_id = cursor.lastrowid

        final_image_name = img_mgr.process_and_register_image(image_url, new_item_id)
        if final_image_name:
            cursor.execute("UPDATE items SET image_url = ? WHERE id = ?", (final_image_name, new_item_id))

        db.commit()
        db.close()
        flash(request, f"'{name_clean}' added.", "success")
        
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


@app.post("/edit-item/{item_id}")
async def edit_item(
    request: Request,
    item_id: int,
    name: str = Form(...),
    description: str = Form(""),
    quantity: int = Form(1),
    container_id: str = Form(...),
    category_id: Optional[str] = Form(None),
    image_url: Optional[str] = Form("")
):
    name_clean = name.strip()
    desc_clean = description.strip()
    cat_val = int(category_id) if category_id and category_id.isdigit() else None
    
    if name_clean and container_id:
        final_image_name = img_mgr.process_and_register_image(image_url, item_id)

        db = db_module.get_db()
        db.execute(
            """UPDATE items 
               SET name = ?, description = ?, quantity = ?, container_id = ?, category_id = ?, image_url = ?
               WHERE id = ?""",
            (name_clean, desc_clean, quantity, container_id, cat_val, final_image_name, item_id)
        )
        db.commit()
        db.close()
        flash(request, f"'{name_clean}' updated.", "success")
        
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


@app.post("/delete-item/{item_id}")
async def delete_item(request: Request, item_id: int):
    db = db_module.get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    db.close()
    flash(request, "Item deleted.", "success")
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


@app.get("/containers", response_class=HTMLResponse)
async def manage_containers(request: Request):
    config = load_config()
    db = db_module.get_db()
    
    containers = db.execute("SELECT * FROM containers ORDER BY type_code ASC, main_num ASC, sub_num ASC").fetchall()
    
    # Elemek lekérése tárolónként csoportosítva
    all_items = db.execute("SELECT id, name, description, quantity, container_id FROM items").fetchall()
    container_items = {}
    for item in all_items:
        c_id = item['container_id']
        if c_id not in container_items:
            container_items[c_id] = []
        container_items[c_id].append(item)

    type_names = {
        "CB": "Cardboard Box",
        "DW": "Drawer Unit",
        "SE": "Shelf / Cabinet",
        "PB": "Plastic Box",
        "OT": "Other Container"
    }
    
    tree = {}
    for c in containers:
        tc = c['type_code']
        main_n = c['main_num']
        
        if tc not in tree:
            tree[tc] = {
                "type_name": type_names.get(tc, tc),
                "mains": {}
            }
            
        if main_n not in tree[tc]["mains"]:
            tree[tc]["mains"][main_n] = {
                "main_container": c if c['sub_num'] is None else {"id": f"{tc}{main_n}", "type_code": tc, "main_num": main_n, "description": ""},
                "sub_containers": []
            }
        
        if c['sub_num'] is None:
            tree[tc]["mains"][main_n]["main_container"] = c
        else:
            tree[tc]["mains"][main_n]["sub_containers"].append(c)

    db.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="containers.html", 
        context={
            "tree": tree,
            "container_items": container_items,
            "mode": config.get("mode")
        }
    )

@app.post("/edit-container/{container_id}")
async def edit_container(
    request: Request,
    container_id: str,
    type_code: str = Form(...),
    main_num: int = Form(...),
    sub_num: Optional[int] = Form(None),
    description: str = Form("")
):
    # Generáljuk az új azonosítót (pl. PB5-1 -> PB5-7)
    new_id = f"{type_code}{main_num}" + (f"-{sub_num}" if sub_num else "")
    db = db_module.get_db()
    
    try:
        # 1. Ha megváltozott az azonosító, először a benne lévő alkatrészeket mozgatjuk az új ID alá
        if new_id != container_id:
            db.execute("UPDATE items SET container_id = ? WHERE container_id = ?", (new_id, container_id))
        
        # 2. Utána frissítjük maga a konténer adatait
        db.execute(
            "UPDATE containers SET id = ?, type_code = ?, main_num = ?, sub_num = ?, description = ? WHERE id = ?",
            (new_id, type_code, main_num, sub_num, description.strip(), container_id)
        )

        db.commit()
        flash(request, f"Konténer átnevezve: '{container_id}' ➔ '{new_id}' (A tartalom megmaradt)", "success")
    except Exception as e:
        db.rollback()
        flash(request, f"Hiba a konténer módosításakor: {e}", "danger")
    finally:
        db.close()

    return RedirectResponse(url="/containers", status_code=303)


@app.post("/delete-container/{container_id}")
async def delete_container(request: Request, container_id: str):
    db = db_module.get_db()
    
    # Ellenőrizzük, hogy van-e benne alkatrész
    item_count = db.execute("SELECT COUNT(*) FROM items WHERE container_id = ?", (container_id,)).fetchone()[0]
    if item_count > 0:
        flash(request, f"Cannot delete container '{container_id}': it contains {item_count} items!", "danger")
    else:
        db.execute("DELETE FROM containers WHERE id = ?", (container_id,))
        db.commit()
        flash(request, f"Container '{container_id}' deleted.", "success")
        
    db.close()
    return RedirectResponse(url="/containers", status_code=303)


@app.post("/add-container")
async def add_container(
    request: Request,
    type_code: str = Form(...),
    main_num: int = Form(...),
    sub_num: Optional[int] = Form(None),
    description: str = Form(""),
    redirect_to: str = Form("containers")
):
    container_id = f"{type_code}{main_num}" + (f"-{sub_num}" if sub_num else "")
    db = db_module.get_db()
    try:
        db.execute(
            "INSERT INTO containers (id, type_code, main_num, sub_num, description) VALUES (?, ?, ?, ?, ?)",
            (container_id, type_code, main_num, sub_num, description.strip())
        )
        db.commit()
        flash(request, f"Container '{container_id}' created.", "success")
    except Exception as e:
        flash(request, f"Error adding container: {e}", "danger")
    finally:
        db.close()
        
    return RedirectResponse(url=f"/{redirect_to}", status_code=303)


@app.get("/categories", response_class=HTMLResponse)
async def manage_categories(request: Request):
    config = load_config()
    db = db_module.get_db()
    
    mains = db.execute("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name ASC").fetchall()
    
    category_tree = []
    for m in mains:
        subs = db.execute("""
            SELECT c.*, COUNT(i.id) as item_count 
            FROM categories c 
            LEFT JOIN items i ON c.id = i.category_id 
            WHERE c.parent_id = ? 
            GROUP BY c.id 
            ORDER BY c.name ASC
        """, (m['id'],)).fetchall()
        
        category_tree.append({
            "id": m['id'],
            "name": m['name'],
            "subs": subs
        })
        
    db.close()
    
    return templates.TemplateResponse(
        request=request, 
        name="categories.html", 
        context={
            "mains": mains,
            "category_tree": category_tree,
            "mode": config.get("mode")
        }
    )


@app.post("/add-category")
async def add_category(
    request: Request,
    name: str = Form(...),
    parent_id: Optional[str] = Form(None)
):
    name_clean = name.strip()
    p_id = int(parent_id) if parent_id and parent_id.isdigit() else None
    
    if name_clean:
        db = db_module.get_db()
        db.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", (name_clean, p_id))
        db.commit()
        db.close()
        flash(request, f"Category '{name_clean}' created.", "success")
        
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/delete-category/{cat_id}")
async def delete_category(request: Request, cat_id: int):
    db = db_module.get_db()
    db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    db.commit()
    db.close()
    flash(request, "Category deleted.", "success")
    return RedirectResponse(url="/categories", status_code=303)


@app.get("/backup")
async def download_backup(request: Request):
    db = db_module.get_db()
    db_path = None
    for row in db.execute("PRAGMA database_list;").fetchall():
        if row[1] == "main" and row[2]:
            db_path = row[2]
            break
    db.close()
    if db_path and os.path.exists(db_path):
        return FileResponse(path=db_path, filename="amazonas_storage_backup.db", media_type="application/x-sqlite3")
    return RedirectResponse(url="/", status_code=303)


@app.get("/sync-images")
async def trigger_image_sync(request: Request):
    threading.Thread(target=img_mgr.sync_all_db_images, daemon=True).start()
    flash(request, "Background image sync started!", "info")
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)