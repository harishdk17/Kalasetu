import logging
import os
import requests
import json
from gtts import gTTS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

PHOTO, VOICE, DECISION, WAITING_FOR_EDIT, WAITING_FOR_AI_PRICE_CONFIRM = range(5)

BOT_TOKEN = "8982329025:AAG40hosPGyNokyiQFw0a8UgB_4oaua-cDI"
FASTAPI_ENDPOINT = "http://127.0.0.1:8000/api/artisan-onboarding"
PUBLISH_ENDPOINT = "http://127.0.0.1:8000/api/publish-product"
EDIT_ENDPOINT = "http://127.0.0.1:8000/api/edit-listing"

def build_decision_keyboard(ai_suggested_price: float) -> InlineKeyboardMarkup:
    price_label = f"💡 Match Fair Price (₹{int(ai_suggested_price)})" if ai_suggested_price else "💡 Match Fair Price"
    keyboard = [
        [
            InlineKeyboardButton("🚀 Publish My Listing", callback_data="btn_publish"),
            InlineKeyboardButton(price_label, callback_data="btn_ai_price"),
        ],
        [
            InlineKeyboardButton("✏️ Change Something", callback_data="btn_edit"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_review_message(product: dict) -> str:
    valuation = product.get("fair_price_estimation", {})
    quoted_price = product.get("price_quoted_by_seller")
    tags = ", ".join(product.get("tags", [])) if isinstance(product.get("tags"), list) else ""
    
    return (
        f"✨ *Here is what your listing looks like:*\n\n"
        f"📌 *Title:* {product.get('title', 'N/A')}\n\n"
        f"📝 *Description:*\n{product.get('description', 'N/A')}\n\n"
        f"🛠️ *Materials Used:* {product.get('materials_used', 'N/A')}\n"
        f"🧑‍🌾 *About You:* {product.get('about_artisan', 'N/A')}\n"
        f"📂 *Category:* {product.get('category', 'N/A')}\n"
        f"🏷️ *Tags:* {tags}\n\n"
        f"-----------------------------------\n\n"
        f"💰 *Price Breakdown*\n"
        f"• *Your Price:* ₹{quoted_price if quoted_price else 'Not mentioned'}\n"
        f"• *Suggested Market Range:* ₹{valuation.get('estimated_min_price', 'N/A')} - ₹{valuation.get('estimated_max_price', 'N/A')}\n"
        f"• *Recommended Fair Price:* ₹{valuation.get('suggested_price', 'N/A')}\n\n"
        f"👇 *What would you like to do next?*"
    )

def create_ai_voice_note(text: str, lang_code: str) -> str:
    audio_path = "ai_response.mp3"
    try:
        if not lang_code or len(lang_code) > 5:
            lang_code = "en"
        tts = gTTS(text=text, lang=lang_code, slow=False)
        tts.save(audio_path)
        return audio_path
    except Exception as e:
        print(f"⚠️ Voice generation warning: {e}")
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(audio_path)
        return audio_path

async def send_review_with_voice(update: Update, context: ContextTypes.DEFAULT_TYPE, product: dict):
    script_text = product.get("audio_response_script", "Hey there! Your listing is ready. Take a look and let me know if you want to publish or change anything.")
    lang_code = product.get("detected_language_code", "en")
    
    voice_file_path = create_ai_voice_note(script_text, lang_code)
    
    try:
        if os.path.exists(voice_file_path):
            with open(voice_file_path, 'rb') as audio:
                await update.message.reply_audio(
                    audio=audio, 
                    title="Voice Note from your Assistant", 
                    performer="Marketplace",
                    caption="🎧 Tap to listen to your voice update"
                )
    except Exception as e:
        print(f"⚠️ Failed to send audio message: {e}")
    finally:
        if os.path.exists(voice_file_path):
            os.remove(voice_file_path)

    valuation = product.get("fair_price_estimation", {})
    ai_price = valuation.get('suggested_price', 0)
    review_msg = format_review_message(product)
    
    await update.message.reply_markdown(review_msg, reply_markup=build_decision_keyboard(ai_price))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Hey there! 👋 Send me a quick photo of your handmade piece to get started.")
    return PHOTO

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    photo_path = "temp_photo.jpg"
    await photo_file.download_to_drive(photo_path)
    context.user_data['photo_path'] = photo_path
    
    await update.message.reply_text(
        "Got it! 📸 Now, just record a quick voice note telling me about your piece—what it is, how you made it, and your price."
    )
    return VOICE

async def receive_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    voice_path = "temp_voice.ogg"
    mime_type = "audio/ogg"
    file_name = "voice.ogg"

    if update.message.voice:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(voice_path)
    elif update.message.audio:
        audio_file = await update.message.audio.get_file()
        file_name = update.message.audio.file_name or "audio.m4a"
        if file_name.endswith(".m4a"):
            voice_path = "temp_voice.m4a"
            mime_type = "audio/mp4"
        await audio_file.download_to_drive(voice_path)
    else:
        await update.message.reply_text("Hmm, I couldn't catch that audio. Could you try sending your voice note again?")
        return VOICE

    await update.message.reply_text("Listening to your note and putting everything together for you... ⏳")
    photo_path = context.user_data.get('photo_path')
    
    try:
        with open(photo_path, 'rb') as pf, open(voice_path, 'rb') as vf:
            files = {
                'photo_file': ('photo.jpg', pf, 'image/jpeg'),
                'audio_file': (file_name, vf, mime_type)
            }
            response = requests.post(FASTAPI_ENDPOINT, files=files, timeout=90)
            
        if response.status_code == 200:
            product = response.json().get("product_data", {})
            context.user_data['raw_product_data'] = product
            await send_review_with_voice(update, context, product)
            return DECISION
        else:
            print(f"🛑 FATAL BACKEND ERROR ({response.status_code}): {response.text}")
            await update.message.reply_text("Ah, something went wrong on our server. Let's try starting over with /start")
            return ConversationHandler.END
    except Exception as e:
        print(f"🛑 CONNECTION ERROR TO BACKEND: {e}")
        await update.message.reply_text("Had trouble connecting to the FastAPI server. Make sure it's running and try /start")
        return ConversationHandler.END
    finally:
        if photo_path and os.path.exists(photo_path): 
            os.remove(photo_path)
        if os.path.exists(voice_path): 
            os.remove(voice_path)

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "btn_publish":
        raw_product = context.user_data.get('raw_product_data', {})
        valuation = raw_product.get("fair_price_estimation", {})
        quoted_price = raw_product.get("price_quoted_by_seller")
        suggested_price = valuation.get('suggested_price') or quoted_price or 0.0

        publish_payload = {
            "title": raw_product.get('title', 'Artisan Product'),
            "description": raw_product.get('description', ''),
            "materials_used": raw_product.get('materials_used', ''),
            "about_artisan": raw_product.get('about_artisan', ''),
            "category": raw_product.get('category', 'Crafts'),
            "price": float(suggested_price),
            "image_url": raw_product.get('hosted_image_url', ''),
            "tags": raw_product.get('tags', []) if isinstance(raw_product.get('tags'), list) else []
        }

        try:
            res = requests.post(PUBLISH_ENDPOINT, json=publish_payload, timeout=10)
            if res.status_code == 200:
                await query.edit_message_text(
                    f"🎉 *Woohoo! Your product is officially live!*\n\n"
                    f"📌 *{publish_payload['title']}* is now on the marketplace for ₹{publish_payload['price']}.\n\n"
                    f"Type /start whenever you want to add another piece!",
                    parse_mode="Markdown"
                )
            else:
                await query.message.reply_text("Couldn't publish right now. Please try clicking the button again.")
        except Exception as e:
            await query.message.reply_text(f"Connection hiccup while publishing: {str(e)}")
        return ConversationHandler.END

    elif query.data == "btn_ai_price":
        raw_product = context.user_data.get('raw_product_data', {})
        valuation = raw_product.get("fair_price_estimation", {})
        ai_price = valuation.get('suggested_price', 0)
        await query.message.reply_text(f"Would you like me to update your price to our recommended **₹{ai_price}**?\n\nJust reply with **'yes'** or send a quick voice note to confirm!", parse_mode="Markdown")
        return WAITING_FOR_AI_PRICE_CONFIRM

    elif query.data == "btn_edit":
        await query.message.reply_text("✏️ Sure thing! Tell me what you'd like to change—type it out or send a voice note (e.g., *'Change the description to mention pure cotton'*).")
        return WAITING_FOR_EDIT

async def confirm_ai_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    is_confirmed = False
    if update.message.text and update.message.text.strip().lower() in ["yes", "yep", "ok", "sure", "confirm"]:
        is_confirmed = True

    raw_product = context.user_data.get('raw_product_data', {})
    valuation = raw_product.get("fair_price_estimation", {})
    ai_price = valuation.get('suggested_price', 0)

    if is_confirmed:
        raw_product["price_quoted_by_seller"] = ai_price
        if "fair_price_estimation" in raw_product:
            raw_product["fair_price_estimation"]["suggested_price"] = ai_price
        context.user_data['raw_product_data'] = raw_product
        await update.message.reply_text(f"✅ All set! Updated your price to **₹{ai_price}**.", parse_mode="Markdown")
    else:
        await update.message.reply_text("No worries, keeping your original price.")

    await send_review_with_voice(update, context, raw_product)
    return DECISION

async def apply_user_edits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    instruction = update.message.text.strip() if update.message.text else "Update the listing"
    await update.message.reply_text("Got it, updating your listing now...", parse_mode="Markdown")
    
    current_product = context.user_data.get('raw_product_data', {})
    
    try:
        edit_payload = {"current_product": current_product, "edit_instruction": instruction}
        res = requests.post(EDIT_ENDPOINT, json=edit_payload, timeout=30)
        
        if res.status_code == 200:
            updated_product = res.json().get("updated_product", {})
            if "hosted_image_url" in current_product:
                updated_product["hosted_image_url"] = current_product["hosted_image_url"]
                
            context.user_data['raw_product_data'] = updated_product
            await send_review_with_voice(update, context, updated_product)
            return DECISION
        else:
            await update.message.reply_text("Hmm, had a little trouble updating that. Try telling me again?")
            return DECISION
    except Exception as e:
        await update.message.reply_text(f"Connection error: {str(e)}")
        return DECISION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("No problem at all. Type /start whenever you're ready to try again!")
    return ConversationHandler.END

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
            VOICE: [MessageHandler(filters.VOICE | filters.AUDIO, receive_voice)],
            DECISION: [CallbackQueryHandler(handle_button_click)],
            WAITING_FOR_EDIT: [MessageHandler((filters.TEXT | filters.VOICE | filters.AUDIO) & ~filters.COMMAND, apply_user_edits)],
            WAITING_FOR_AI_PRICE_CONFIRM: [MessageHandler((filters.TEXT | filters.VOICE | filters.AUDIO) & ~filters.COMMAND, confirm_ai_price)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    print("🤖 Telegram Bot is running and waiting for messages...")
    application.run_polling()

if __name__ == "__main__":
    main()