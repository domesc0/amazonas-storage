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

import database as db_module

app = FastAPI(title="AMAZONAS STORAGE SYSTEM")
app.add_middleware(SessionMiddleware, secret_key="amazonas_secret_key_123")

# Statikus mappák beállítása
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Assets mappa és al-mappák biztosítása
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

def flash(request: Request, message: str, category: str = "success"):
    if "flash_messages" not in request.session:
        request.session["flash_messages"] = []
    request.session["flash_messages"].append({"message": message, "category": category})

def get_flashed_messages(request: Request):
    return request.session.pop("flash_messages", [])

templates.env.globals["get_flashed_messages"] = get_flashed_messages


# --- ÚTVONALAK ---

@app.get("/toggle-mode")
async def toggle_mode(request: Request):
    config = load_config()
    config["mode"] = "edit" if config.get("mode") == "inventory" else "inventory"
    save_config(config)
    flash(request, f"Switched to {config['mode'].upper()} Mode.", "info")
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, q: Optional[str] = None, page: int = Query(1, ge=1)):
    search_query = (q or "").strip()
    config = load_config()
    current_mode = config.get("mode", "inventory")
    
    # ✏️ Edit módban 3 elem, 🔍 Inventory módban 20 elem / oldal
    items_per_page = 3 if current_mode == "edit" else 20

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

    # Összes elem kiszámítása
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
            "total_found": total_found
        }
    )


@app.get("/containers", response_class=HTMLResponse)
async def manage_containers(request: Request):
    config = load_config()
    db = db_module.get_db()
    containers = db.execute("SELECT * FROM containers ORDER BY type_code ASC, main_num ASC, sub_num ASC").fetchall()
    
    item_counts = {}
    counts_raw = db.execute("SELECT container_id, COUNT(*) as count FROM items GROUP BY container_id").fetchall()
    for row in counts_raw:
        item_counts[row["container_id"]] = row["count"]
        
    type_names = {
        "CB": "Cardboard Box",
        "DW": "Drawer Unit",
        "SE": "Shelf / Cabinet",
        "PB": "Plastic Box",
        "OT": "Other Container"
    }
    
    tree = {}
    for c in containers:
        tc = c["type_code"]
        mn = c["main_num"]
        
        if tc not in tree:
            tree[tc] = {"type_code": tc, "type_name": type_names.get(tc, tc), "mains": {}}
            
        if mn not in tree[tc]["mains"]:
            main_id = f"{tc}{mn}"
            # ÚJ: az assets/storage/ mappában keresi a képet!
            has_image = os.path.exists(os.path.join("assets", "storage", f"{main_id}.png")) or \
                        os.path.exists(os.path.join("assets", "storage", f"{main_id}.jpg"))
            
            ext = "png" if os.path.exists(os.path.join("assets", "storage", f"{main_id}.png")) else "jpg"
            
            tree[tc]["mains"][mn] = {
                "main_num": mn,
                "main_id": main_id,
                "has_image": has_image,
                "image_ext": ext,
                "sub_containers": [],
                "main_container": None
            }
            
        if c["sub_num"] is not None:
            tree[tc]["mains"][mn]["sub_containers"].append(c)
        else:
            tree[tc]["mains"][mn]["main_container"] = c

    db.close()

    return templates.TemplateResponse(
        request=request,
        name="containers.html",
        context={"tree": tree, "containers": containers, "item_counts": item_counts, "mode": config.get("mode", "inventory")}
    )

# ... (többi szerkesztő/törlő/kategória útvonal változatlan) ...


@app.get("/categories", response_class=HTMLResponse)
async def manage_categories(request: Request):
    config = load_config()
    db = db_module.get_db()
    mains = db.execute("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name ASC").fetchall()
    
    category_tree = []
    for m in mains:
        subs = db.execute("SELECT * FROM categories WHERE parent_id = ? ORDER BY name ASC", (m["id"],)).fetchall()
        sub_list = []
        for s in subs:
            count = db.execute("SELECT COUNT(*) FROM items WHERE category_id = ?", (s["id"],)).fetchone()[0]
            sub_list.append({"id": s["id"], "name": s["name"], "item_count": count})
            
        category_tree.append({"id": m["id"], "name": m["name"], "subs": sub_list})
        
    db.close()
    return templates.TemplateResponse(
        request=request,
        name="categories.html",
        context={"category_tree": category_tree, "mains": mains, "mode": config.get("mode", "inventory")}
    )


# --- API ENDPOINT AZ ALKATRÉSZ KÉP / SPEC LEKÉRÉSÉHEZ (Octopart/Open API mock/integration) ---
@app.get("/fetch-specs")
async def fetch_component_specs(mpn: str = Query(...)):
    """Lekéri az IC képét és adatlapját cikkszám alapján."""
    clean_mpn = mpn.strip().upper()
    
    # Nyílt alkatrész-kereső API segítségével lekérjük a képet/adatlapot
    # Példa automatikus képgenerálásra és adatokra:
    sample_data = {
        "mpn": clean_mpn,
        "image_url": f"https://img.farnell.com/is/image/farnell/{clean_mpn}?$prd-main$",
        "datasheet_url": f"https://www.alldatasheet.com/view.jsp?Searchword={clean_mpn}"
    }
    return JSONResponse(sample_data)


# --- MŰVELETI ÚTVONALAK ---

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
        db.execute(
            "INSERT INTO items (name, description, quantity, container_id, category_id, image_url) VALUES (?, ?, ?, ?, ?, ?)",
            (name_clean, desc_clean, quantity, container_id, cat_val, image_url.strip())
        )
        db.commit()
        db.close()
        flash(request, f"Item '{name_clean}' added.", "success")
        
    return RedirectResponse(url="/", status_code=303)


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
        db = db_module.get_db()
        db.execute(
            """UPDATE items 
               SET name = ?, description = ?, quantity = ?, container_id = ?, category_id = ?, image_url = ?
               WHERE id = ?""",
            (name_clean, desc_clean, quantity, container_id, cat_val, image_url.strip(), item_id)
        )
        db.commit()
        db.close()
        flash(request, f"Item '{name_clean}' updated.", "success")
        
    return RedirectResponse(url="/", status_code=303)


@app.post("/delete-item/{item_id}")
async def delete_item(request: Request, item_id: int):
    db = db_module.get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    db.close()
    flash(request, "Item deleted.", "success")
    return RedirectResponse(url="/", status_code=303)


@app.post("/add-category")
async def add_category(request: Request, name: str = Form(...), parent_id: Optional[str] = Form(None)):
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
    items_count = db.execute("SELECT COUNT(*) FROM items WHERE category_id = ?", (cat_id,)).fetchone()[0]
    if items_count > 0:
        flash(request, f"Cannot delete category! Assigned to {items_count} item(s).", "error")
    else:
        db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        db.commit()
        flash(request, "Category deleted.", "success")
    db.close()
    return RedirectResponse(url="/categories", status_code=303)


@app.post("/add-container")
async def add_container(
    request: Request,
    type_code: str = Form("OT"),
    main_num: int = Form(...),
    sub_num: Optional[str] = Form(None),
    description: str = Form(""),
    redirect_to: str = Form("index")
):
    tc_clean = type_code.upper().strip()
    desc_clean = description.strip()
    if tc_clean and main_num:
        sub_val = int(sub_num.strip()) if sub_num and sub_num.strip() else None
        container_id = f"{tc_clean}{main_num}-{sub_val}" if sub_val is not None else f"{tc_clean}{main_num}"
        db = db_module.get_db()
        try:
            db.execute("INSERT INTO containers (id, type_code, main_num, sub_num, description) VALUES (?, ?, ?, ?, ?)",
                       (container_id, tc_clean, main_num, sub_val, desc_clean))
            db.commit()
            flash(request, f"Container '{container_id}' created.", "success")
        except db_module.sqlite3.IntegrityError:
            flash(request, f"Container '{container_id}' already exists!", "error")
        finally:
            db.close()
    target_url = "/containers" if redirect_to == "containers" else "/"
    return RedirectResponse(url=target_url, status_code=303)


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
    else:
        flash(request, "Database file not found!", "error")
        return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)