"""
media_handler.py
Handles image encoding for Gemini Vision and voice note metadata.
"""

import base64
import os


def encode_image_base64(image_path: str) -> str | None:
    """Read image file and return base64-encoded string, or None if not found."""
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def get_image_mime(image_path: str) -> str:
    """Return MIME type based on file extension."""
    ext = os.path.splitext(image_path)[-1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def get_voice_note_description(voice_note_path: str) -> str:
    """
    Returns a placeholder note about the voice note.
    Full transcription would require a speech-to-text API call;
    for now we return the path so the LLM knows media exists.
    """
    if not voice_note_path:
        return "[voice note: path not found]"
    filename = os.path.basename(voice_note_path)
    size_kb = ""
    if os.path.exists(voice_note_path):
        size_bytes = os.path.getsize(voice_note_path)
        size_kb = f", ~{size_bytes // 1024} KB"
    return f"[voice note file: {filename}{size_kb}]"


def prepare_media_for_gemini(msg: dict, ctx) -> dict:
    """
    Given a message row and DataContext, return a dict with:
      - image_b64 (str | None): base64 image data
      - image_mime (str | None): MIME type
      - voice_note_desc (str | None): voice note description
    """
    media_type = msg.get("media_type", "")
    media_id = msg.get("media_id", "")

    result = {
        "image_b64": None,
        "image_mime": None,
        "voice_note_desc": None,
    }

    if media_type == "image" and media_id:
        path = ctx.get_image_path(media_id)
        if path:
            result["image_b64"] = encode_image_base64(path)
            result["image_mime"] = get_image_mime(path)

    elif media_type == "voice" and media_id:
        path = ctx.get_voice_note_path(media_id)
        result["voice_note_desc"] = get_voice_note_description(path or "")

    return result
