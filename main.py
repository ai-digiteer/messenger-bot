from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os
from dotenv import load_dotenv
import requests
import logging
import asyncio
import time
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN", "myverifytoken")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
DX_API_SEND_MESSAGE = os.getenv("DX_API_SEND_MESSAGE")

FB_MESSENGER_API = "https://graph.facebook.com/v21.0/me/messages"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

sender_map = {}
SESSION_TIMEOUT = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def cleanup_sessions():
        while True:
            now = time.time()
            expired = [
                chat_id for chat_id, info in sender_map.items()
                if now - info["last_active"] > SESSION_TIMEOUT
            ]
            for chat_id in expired:
                logger.info(f"[SESSION EXPIRED] Removing chat_id: {chat_id}")
                sender_map.pop(chat_id, None)
            await asyncio.sleep(5)

    cleanup_task = asyncio.create_task(cleanup_sessions())
    yield
    cleanup_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "Running"}


# ---------------------------------------------------------
#  WEBHOOK (GET = verification, POST = messages)
# ---------------------------------------------------------
@app.api_route("/webhook", methods=["GET", "POST"])
async def webhook(request: Request):
    # --------------------------
    # GET: Verification
    # --------------------------
    if request.method == "GET":
        params = dict(request.query_params)
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("[Webhook Verified]")
            return PlainTextResponse(content=challenge, status_code=200)

        logger.warning("Webhook verification failed")
        return PlainTextResponse("Forbidden", status_code=403)

    # --------------------------
    # POST: Incoming messages
    # --------------------------
    body = await request.json()
    logger.info(f"[WEBHOOK EVENT] {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" not in event:
                    continue

                sender_id = event["sender"]["id"]
                message_text = event["message"].get("text")
                attachments = event["message"].get("attachments", [])

                logger.info(f"Message from {sender_id}: {message_text}")

                chat_id = sender_id
                sender_map[chat_id] = {
                    "sender_id": sender_id,
                    "last_active": time.time()
                }

                # Extract file URLs only (if any)
                file_urls = []
                for a in attachments:
                    url = a.get("payload", {}).get("url")
                    if url:
                        file_urls.append(url)

                # Your STATIC file_ids list
                file_ids = [
                    "9914f841-6181-4f74-9832-9c144faeb915",
                    "7232c4fb-b2ec-4653-9478-042d198a4487",
                    "014d7b95-7d9b-4408-8ee3-f1a06e83a423",
                    "1708decb-dbb3-4a9e-81ab-97c6a72d6492",
                    "ae46c4a5-50a1-4e28-9053-48ee36bd650c"
                ]

                # Build payload for DX API
                dx_payload = {
                    "chat_id": chat_id,
                    "user_message": message_text,
                    "file_ids": file_ids,
                    "file_urls": file_urls,
                    "callback_type": "messenger"
                }

                logger.info(f"[DX PAYLOAD] {dx_payload}")

                try:
                    dx_response = requests.post(
                        DX_API_SEND_MESSAGE,
                        json=dx_payload,
                        timeout=5
                    )
                    dx_response.raise_for_status()
                    logger.info(f"DX API Success: {dx_response.status_code}")
                except Exception as e:
                    logger.error(f"DX API Error: {e}")

    return {"status": "ok"}


# ---------------------------------------------------------
# DX CALLBACK → SEND AI RESPONSE BACK TO MESSENGER
# ---------------------------------------------------------
@app.post("/dx-result")
async def receive_dx_result(request: Request):
    data = await request.json()
    logger.info(f"[DX RESULT] {data}")

    ai_response = data.get("ai_response")
    chat_id = data.get("chat_id")

    sender_info = sender_map.get(chat_id)
    sender_id = sender_info["sender_id"] if sender_info else None

    if not sender_id:
        logger.warning(f"No sender found for chat_id: {chat_id}")
        return {"status": "missing-sender"}

    send_payload = {
        "recipient": {"id": sender_id},
        "message": {"text": ai_response},
    }

    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(FB_MESSENGER_API, headers=headers, json=send_payload)
        response.raise_for_status()
        logger.info(f"AI Reply sent to {sender_id}")
    except Exception as e:
        logger.error(f"Messenger Send Error: {e}")

    return {"status": "received"}
