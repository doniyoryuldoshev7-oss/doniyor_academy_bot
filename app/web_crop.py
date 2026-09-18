import json
import os
import time
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from PIL import Image


SESSIONS = {}
SESSION_TTL = 3600


def _cleanup_sessions():
    now = time.time()
    expired = [
        sid
        for sid, item in SESSIONS.items()
        if now - item["created_at"] > SESSION_TTL
    ]
    for sid in expired:
        SESSIONS.pop(sid, None)


def public_base_url():
    value = os.getenv("WEB_APP_URL", "").strip()

    if value:
        return value.rstrip("/")

    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()

    if domain:
        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = "https://" + domain
        return domain.rstrip("/")

    return ""


def create_session(
    *,
    admin_id: int,
    image_path: str,
    crop_dir: str,
    count: int,
):
    _cleanup_sessions()

    session_id = uuid4().hex

    SESSIONS[session_id] = {
        "created_at": time.time(),
        "admin_id": int(admin_id),
        "image_path": str(image_path),
        "crop_dir": str(crop_dir),
        "count": int(count),
        "saved": [],
    }

    return session_id


def get_session(session_id: str):
    _cleanup_sessions()
    return SESSIONS.get(session_id)


def finish_session(session_id: str):
    return SESSIONS.pop(session_id, None)


def web_app_url(session_id: str):
    base = public_base_url()

    if not base:
        return ""

    return f"{base}/crop/{session_id}"


async def health(request):
    return web.json_response({
        "ok": True,
        "service": "doniyor-academy-web",
    })


async def crop_page(request):
    session_id = request.match_info["session_id"]
    session = get_session(session_id)

    if not session:
        return web.Response(
            text="Crop sessiyasi topilmadi yoki muddati tugagan.",
            status=404,
            content_type="text/plain",
        )

    html = r"""<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Doniyor Academy ? Savol belgilash</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* { box-sizing: border-box; }
html, body {
    margin: 0;
    padding: 0;
    background: #111;
    color: #fff;
    font-family: Arial, sans-serif;
}
body {
    padding: 10px;
}
.header {
    position: sticky;
    top: 0;
    z-index: 20;
    background: #111;
    padding: 4px 0 10px;
}
.title {
    font-size: 18px;
    font-weight: 700;
}
.info {
    margin-top: 5px;
    font-size: 14px;
    opacity: .85;
}
.stage {
    position: relative;
    width: 100%;
    overflow: hidden;
    touch-action: none;
    background: #222;
    border-radius: 8px;
}
#source {
    display: block;
    width: 100%;
    height: auto;
    user-select: none;
    -webkit-user-drag: none;
}
#box {
    position: absolute;
    display: none;
    border: 3px solid #ff3b30;
    background: rgba(255, 59, 48, .12);
    pointer-events: none;
}
.controls {
    position: sticky;
    bottom: 0;
    z-index: 20;
    background: #111;
    padding: 10px 0 4px;
}
button {
    width: 100%;
    border: 0;
    border-radius: 10px;
    padding: 14px;
    font-size: 17px;
    font-weight: 700;
    background: #1683f5;
    color: white;
}
button:disabled {
    opacity: .45;
}
#message {
    margin-top: 8px;
    font-size: 13px;
    text-align: center;
    min-height: 18px;
}
</style>
</head>
<body>

<div class="header">
    <div class="title">Doniyor Academy ? Savol belgilash</div>
    <div class="info" id="counter"></div>
</div>

<div class="stage" id="stage">
    <img id="source" draggable="false">
    <div id="box"></div>
</div>

<div class="controls">
    <button id="save" disabled>1-savolni saqlash</button>
    <div id="message"></div>
</div>

<script>
const SESSION_ID = "__SESSION_ID__";
const COUNT = __COUNT__;

const tg = window.Telegram && window.Telegram.WebApp
    ? window.Telegram.WebApp
    : null;

if (tg) {
    tg.ready();
    tg.expand();
}

const stage = document.getElementById("stage");
const img = document.getElementById("source");
const box = document.getElementById("box");
const saveBtn = document.getElementById("save");
const counter = document.getElementById("counter");
const message = document.getElementById("message");

let current = 1;
let startX = 0;
let startY = 0;
let endX = 0;
let endY = 0;
let drawing = false;

function setMessage(text) {
    message.textContent = text;
}

function updateCounter() {
    if (current <= COUNT) {
        counter.textContent =
            current + "-savolni belgilang. Jami: " + COUNT;
        saveBtn.textContent =
            current + "-savolni saqlash";
    }
}

function pointFromEvent(e) {
    const rect = stage.getBoundingClientRect();

    const clientX = e.clientX !== undefined
        ? e.clientX
        : e.touches[0].clientX;

    const clientY = e.clientY !== undefined
        ? e.clientY
        : e.touches[0].clientY;

    return {
        x: Math.max(0, Math.min(rect.width, clientX - rect.left)),
        y: Math.max(0, Math.min(rect.height, clientY - rect.top))
    };
}

function drawBox() {
    const x = Math.min(startX, endX);
    const y = Math.min(startY, endY);
    const w = Math.abs(endX - startX);
    const h = Math.abs(endY - startY);

    box.style.left = x + "px";
    box.style.top = y + "px";
    box.style.width = w + "px";
    box.style.height = h + "px";
    box.style.display = "block";

    saveBtn.disabled = !(w >= 10 && h >= 10);
}

function begin(e) {
    e.preventDefault();

    const p = pointFromEvent(e);
    startX = p.x;
    startY = p.y;
    endX = p.x;
    endY = p.y;
    drawing = true;

    drawBox();
}

function move(e) {
    if (!drawing) return;

    e.preventDefault();

    const p = pointFromEvent(e);
    endX = p.x;
    endY = p.y;

    drawBox();
}

function end(e) {
    if (!drawing) return;

    e.preventDefault();
    drawing = false;
}

stage.addEventListener("pointerdown", begin);
stage.addEventListener("pointermove", move);
stage.addEventListener("pointerup", end);
stage.addEventListener("pointercancel", end);

async function saveCrop() {
    if (!saveBtn.disabled) {
        const rect = stage.getBoundingClientRect();

        const x = Math.min(startX, endX);
        const y = Math.min(startY, endY);
        const w = Math.abs(endX - startX);
        const h = Math.abs(endY - startY);

        const naturalWidth = img.naturalWidth;
        const naturalHeight = img.naturalHeight;

        const scaleX = naturalWidth / rect.width;
        const scaleY = naturalHeight / rect.height;

        const payload = {
            index: current,
            x: Math.round(x * scaleX),
            y: Math.round(y * scaleY),
            width: Math.round(w * scaleX),
            height: Math.round(h * scaleY)
        };

        saveBtn.disabled = true;
        setMessage("Saqlanmoqda...");

        try {
            const response = await fetch(
                "/crop/" + SESSION_ID + "/save",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(payload)
                }
            );

            const result = await response.json();

            if (!response.ok || !result.ok) {
                throw new Error(result.error || "Saqlashda xatolik");
            }

            if (current >= COUNT) {
                counter.textContent =
                    "Barcha " + COUNT + " ta savol tayyor.";

                setMessage(
                    "Tayyor. Telegram oynasiga qayting."
                );

                if (tg) {
                    tg.sendData(JSON.stringify({
                        action: "done",
                        session_id: SESSION_ID
                    }));
                }

                return;
            }

            current += 1;
            box.style.display = "none";
            startX = startY = endX = endY = 0;

            updateCounter();
            setMessage("Keyingi savolni belgilang.");
            saveBtn.disabled = true;

        } catch (error) {
            setMessage(error.message || "Xatolik yuz berdi.");
            saveBtn.disabled = false;
        }
    }
}

saveBtn.addEventListener("click", saveCrop);

img.src = "/crop/" + SESSION_ID + "/image";

img.onload = function() {
    updateCounter();
    setMessage(
        "Savolning to'liq qismini to'rtburchak qilib belgilang."
    );
};
</script>

</body>
</html>
"""

    html = (
        html
        .replace("__SESSION_ID__", session_id)
        .replace("__COUNT__", str(session["count"]))
    )

    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
    )


async def crop_image(request):
    session_id = request.match_info["session_id"]
    session = get_session(session_id)

    if not session:
        return web.Response(status=404, text="Session not found")

    path = Path(session["image_path"])

    if not path.exists():
        return web.Response(status=404, text="Source image not found")

    return web.FileResponse(path)


async def save_crop(request):
    session_id = request.match_info["session_id"]
    session = get_session(session_id)

    if not session:
        return web.json_response(
            {"ok": False, "error": "Sessiya topilmadi."},
            status=404,
        )

    try:
        data = await request.json()

        index = int(data["index"])
        x = int(data["x"])
        y = int(data["y"])
        width = int(data["width"])
        height = int(data["height"])

    except Exception:
        return web.json_response(
            {"ok": False, "error": "Noto'g'ri koordinatalar."},
            status=400,
        )

    if index != len(session["saved"]) + 1:
        return web.json_response(
            {"ok": False, "error": "Savollar tartibi noto'g'ri."},
            status=400,
        )

    if width < 10 or height < 10:
        return web.json_response(
            {"ok": False, "error": "Belgilangan maydon juda kichik."},
            status=400,
        )

    source = Path(session["image_path"])
    crop_dir = Path(session["crop_dir"])
    crop_dir.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source) as image:
            image = image.convert("RGB")

            x1 = max(0, min(image.width - 1, x))
            y1 = max(0, min(image.height - 1, y))
            x2 = max(x1 + 1, min(image.width, x + width))
            y2 = max(y1 + 1, min(image.height, y + height))

            if x2 <= x1 or y2 <= y1:
                raise ValueError("Crop maydoni noto'g'ri.")

            cropped = image.crop((x1, y1, x2, y2))

            output = crop_dir / f"crop_{index}.jpg"
            cropped.save(output, "JPEG", quality=95)

    except Exception as exc:
        return web.json_response(
            {"ok": False, "error": str(exc)},
            status=500,
        )

    session["saved"].append(str(output))

    return web.json_response({
        "ok": True,
        "index": index,
        "done": len(session["saved"]) >= session["count"],
    })


def create_app():
    app = web.Application(client_max_size=20 * 1024 * 1024)

    app.router.add_get("/health", health)
    app.router.add_get("/crop/{session_id}", crop_page)
    app.router.add_get("/crop/{session_id}/image", crop_image)
    app.router.add_post("/crop/{session_id}/save", save_crop)

    return app


async def start_web_server():
    port = int(os.getenv("PORT", "8080"))

    app = create_app()

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    print(
        f">>> WEB CROP SERVER STARTED: 0.0.0.0:{port}",
        flush=True,
    )

    return runner
