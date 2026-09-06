import json
import os
import uuid
import base64
import sqlite3
import requests
import traceback
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Artisan Marketplace Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key=AQ.Ab8RN6IWQe0k0mYXKoXSPLL3CKSHQC9QufSeINSlsqQAH3Ta-w"

UPLOAD_DIR = "static/uploads"
DB_FILE = "marketplace.db"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            materials_used TEXT,
            about_artisan TEXT,
            category TEXT,
            price REAL,
            image_url TEXT,
            tags TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_product_record(product_data: dict) -> dict:
    product_id = product_data.get("id") or str(uuid.uuid4())
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    tags_json = json.dumps(product_data.get("tags", []))
    
    cursor.execute('''
        INSERT OR REPLACE INTO products (id, title, description, materials_used, about_artisan, category, price, image_url, tags, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        product_id,
        product_data.get("title", "Untitled Product"),
        product_data.get("description", ""),
        product_data.get("materials_used", ""),
        product_data.get("about_artisan", ""),
        product_data.get("category", "Uncategorized"),
        product_data.get("price", 0.0),
        product_data.get("image_url", ""),
        tags_json,
        "active"
    ))
    conn.commit()
    conn.close()
    product_data["id"] = product_id
    return product_data

def load_products() -> list:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE status='active'")
    rows = cursor.fetchall()
    conn.close()

    products = []
    for row in rows:
        prod = dict(row)
        prod["tags"] = json.loads(prod["tags"]) if prod["tags"] else []
        products.append(prod)
    return products

def extract_clean_json(text: str) -> dict:
    try:
        text = text.strip()
        if text.startswith("```json"): text = text[7:]
        elif text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text.strip())
    except Exception as e:
        print(f"❌ JSON Parsing Failed! Raw AI Output was:\n{text}")
        raise ValueError(f"AI returned invalid JSON: {str(e)}")

def process_multimodal_valuation(
    audio_bytes: Optional[bytes] = None, 
    audio_mime: str = "audio/ogg",
    image_bytes: Optional[bytes] = None,
    image_mime: str = "image/jpeg"
) -> dict:
    prompt = """
    You are an expert Fair Price Estimation Assistant and e-commerce cataloger for an Indian artisan marketplace.
    Analyze the provided product image and audio to evaluate quality, extract details, and estimate a fair selling price.
    
    Detect the spoken language code (e.g., 'en' for English, 'hi' for Hindi).
    Write a short, warm, and completely human-sounding audio script in that SAME language saying:
    "We have processed your product details. If you're happy with it, you can go ahead and publish it right now. If you want to change any details or edit anything, just let me know. Or, if you want to switch the price to our AI recommended price, you can do that too!"

    Return ONLY valid JSON matching this exact schema:
    {
      "quality_check": {"audio_clear": true, "image_clear": true},
      "detected_language_code": "en",
      "audio_response_script": "Hey there! We've put together your listing...",
      "title": "string",
      "description": "string",
      "materials_used": "string",
      "about_artisan": "string",
      "category": "string",
      "tags": ["string"],
      "price_quoted_by_seller": 1000,
      "fair_price_estimation": {
        "estimated_min_price": 800,
        "estimated_max_price": 1200,
        "suggested_price": 1000
      }
    }
    """
    parts = [{"text": prompt}]

    if audio_bytes:
        encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')
        parts.append({"inline_data": {"mime_type": audio_mime, "data": encoded_audio}})

    if image_bytes:
        encoded_image = base64.b64encode(image_bytes).decode('utf-8')
        parts.append({"inline_data": {"mime_type": image_mime, "data": encoded_image}})

    payload = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2}}

    try:
        response = requests.post(GEMINI_API_URL, json=payload, headers={'Content-Type': 'application/json'}, timeout=60)
        
        if response.status_code != 200:
            print(f"❌ GEMINI API HTTP ERROR: {response.status_code} - {response.text}")
            raise HTTPException(status_code=500, detail="Gemini API rejected the request. Check quota/key.")
        
        result = response.json()
        ai_text = result['candidates'][0]['content']['parts'][0]['text']
        return extract_clean_json(ai_text)

    except requests.exceptions.RequestException as e:
        print(f"❌ NETWORK ERROR reaching Gemini: {str(e)}")
        raise HTTPException(status_code=500, detail="Could not connect to Gemini API over the network.")

@app.post("/api/artisan-onboarding")
async def process_artisan_listing(
    request: Request,
    audio_file: Optional[UploadFile] = File(None),
    photo_file: Optional[UploadFile] = File(None)
):
    try:
        audio_bytes = await audio_file.read() if audio_file else None
        audio_mime = audio_file.content_type or "audio/ogg" if audio_file else "audio/ogg"
        
        image_bytes = None
        saved_image_filename = None
        if photo_file and photo_file.filename:
            image_bytes = await photo_file.read()
            ext = os.path.splitext(photo_file.filename)[1] or ".jpg"
            saved_image_filename = f"{uuid.uuid4()}{ext}"
            with open(os.path.join(UPLOAD_DIR, saved_image_filename), "wb") as f:
                f.write(image_bytes)

        print("🚀 Sending files to Gemini for processing...")
        structured_json = process_multimodal_valuation(
            audio_bytes=audio_bytes, audio_mime=audio_mime,
            image_bytes=image_bytes, image_mime=photo_file.content_type if photo_file else "image/jpeg"
        )
        print("✅ Gemini successfully returned JSON data!")

        if saved_image_filename:
            base_url = str(request.base_url).rstrip('/')
            full_image_url = f"{base_url}/static/uploads/{saved_image_filename}"
            structured_json["hosted_image_url"] = full_image_url
            structured_json["image_url"] = full_image_url

        return {"status": "success", "product_data": structured_json}

    except Exception as e:
        print("🛑 FATAL SERVER ERROR DURING ONBOARDING:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

class EditRequest(BaseModel):
    current_product: dict
    edit_instruction: str

@app.post("/api/edit-listing")
async def edit_listing(request: EditRequest):
    try:
        prompt = f"""
        You are an expert e-commerce catalog editor. Update this listing based on user instructions.
        CURRENT LISTING: {json.dumps(request.current_product)}
        USER INSTRUCTION: "{request.edit_instruction}"
        Return ONLY valid JSON matching the exact same schema structure.
        """
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
        response = requests.post(GEMINI_API_URL, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Gemini failed during edit.")
            
        result = response.json()
        ai_text = result['candidates'][0]['content']['parts'][0]['text']
        updated_json = extract_clean_json(ai_text)
        return {"status": "success", "updated_product": updated_json}
    except Exception as e:
        print("🛑 FATAL ERROR DURING EDIT:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class PublishRequest(BaseModel):
    title: str
    description: str
    materials_used: str
    about_artisan: str
    category: str
    price: float
    image_url: str
    tags: List[str] = []

@app.post("/api/publish-product")
async def publish_product(item: PublishRequest):
    saved_record = save_product_record(item.dict())
    return {"status": "success", "message": "Product published!", "product": saved_record}

@app.get("/api/products")
async def get_all_products():
    return {"status": "success", "count": len(load_products()), "products": load_products()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)