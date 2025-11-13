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

# Store sender_id and timestamp
# sender_map = {chat_id: {"sender_id": <id>, "last_active": <timestamp>}}
sender_map = {}
SESSION_TIMEOUT = 300  


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown tasks."""
    async def cleanup_sessions():
        logger.info("[CLEANUP TASK] Checking for expired sessions...")
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

    # Startup
    cleanup_task = asyncio.create_task(cleanup_sessions())
    logger.info("🟢 Session cleanup task started.")

    yield  # App runs here

    # Shutdown
    cleanup_task.cancel()
    logger.info("🔴 Session cleanup task stopped.")


# ✅ Attach lifespan to FastAPI
app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "Running"}


@app.get("/verify")
async def verify(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("[Webhook Verified]")
        return PlainTextResponse(content=challenge, status_code=200)
    else:
        return PlainTextResponse("Forbidden", status_code=403)


@app.post("/webhook")
async def handle_messages(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                if "message" in messaging_event:
                    sender_id = messaging_event["sender"]["id"]
                    message_text = messaging_event["message"].get("text")
                    logger.info(f"Message from {sender_id}: {message_text}")

                    if message_text:
                        chat_id = sender_id
                        sender_map[chat_id] = {
                            "sender_id": sender_id,
                            "last_active": time.time()
                        }
                        logger.info(f"Updated sender_map: {sender_map}")

                        dx_payload = {
                            "chat_id": chat_id,
                            "user_message": message_text,
                            "file_ids": [
                                "4eb53f62-d860-457f-bc00-fee11b31f190",
                                "a933342b-8332-492e-a494-8e676af0ac0e",
                                "b4d6024c-8bdb-4bfe-ab5c-abaea50b6461",
                                "bff88d57-c61a-4e41-9295-3839ae47656a",
                                "1b48046c-756a-49f5-af68-a615fcf520a7",
                                "5bf8a7fb-9126-42d4-b405-1b909c656854",
                                "7f63b10d-5073-4414-a7fa-2526d1526044",
                                "1dae7954-a9c0-4966-9916-3e192d6c23f9",
                                "a126c441-b0d9-4842-9d1e-d72182f5dffb",
                                "b50962d3-8006-49bf-9502-34bcb6f19213"
                            ],
                            "file_urls": [],
                            "callback_type": "messenger"
                        }

                        try:
                            dx_response = requests.post(
                                DX_API_SEND_MESSAGE,
                                json=dx_payload,
                                timeout=5
                            )
                            dx_response.raise_for_status()
                            logger.info(f"Sent message to DX API: {dx_response.status_code}")
                        except requests.RequestException as e:
                            logger.error(f"Failed to send message to DX API: {e}")

    return {"status": "ok"}


@app.post("/dx-result")
async def receive_dx_result(request: Request):
    try:
        data = await request.json()
        logger.info(f"[DX RESULT] Received: {data}")

        ai_response = data.get("ai_response")
        chat_id = data.get("chat_id")

        sender_info = sender_map.get(chat_id)
        sender_id = sender_info["sender_id"] if sender_info else None

        if sender_id and ai_response:
            send_payload = {
                "recipient": {"id": sender_id},
                "message": {"text": ai_response},
            }
            headers = {
                "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }

            logger.info(f"Sending AI reply to {sender_id}: {ai_response}")
            response = requests.post(FB_MESSENGER_API, headers=headers, json=send_payload)
            response.raise_for_status()
            logger.info(f"Sent AI reply successfully to {sender_id}")

        else:
            logger.warning(f"Sender ID not found or session expired for chat_id: {chat_id}")

        return {"status": "received"}

    except requests.RequestException as e:
        logger.error(f"Failed sending AI reply: {e}")
        if e.response is not None:
            logger.error(f"Facebook status code: {e.response.status_code}")
            logger.error(f"Facebook response: {e.response.text}")
