"""
llm_router.py
Multi-backend LLM router: tries Ollama first (local), falls back to Google Gemini if available.
Backend is selected via LLM_BACKEND env var ("ollama" or "google").

Ollama models used:
  - llava:7b  for image messages (multimodal)
  - llama3.2  for text-only messages

Google: gemini-2.0-flash (requires GOOGLE_API_KEY)
"""

import json
import re
import time
import os
import urllib.request
import urllib.error
from prompts import SYSTEM_PROMPT, FEW_SHOT_EXAMPLES

# Valid enum values
VALID_ACTIONS = {"notify", "digest", "mute"}
VALID_TYPES = {
    "personal", "urgent", "event", "payment", "business_update",
    "promotion", "greeting", "forward", "spam", "scam", "unknown",
}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TEXT_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "llama3.2")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llava:7b")


def _ollama_available() -> bool:
    """Check if Ollama is running."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _google_available() -> bool:
    """Check if Google API key is set."""
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip())


class LLMRouter:
    def __init__(self, api_key: str | None = None, model_name: str = "gemini-2.0-flash"):
        backend_pref = os.environ.get("LLM_BACKEND", "auto").lower()

        # Determine backend
        if backend_pref == "google" and _google_available():
            self.backend = "google"
        elif backend_pref == "ollama" and _ollama_available():
            self.backend = "ollama"
        elif backend_pref == "auto":
            # auto: try ollama first, then google
            if _ollama_available():
                self.backend = "ollama"
            elif _google_available():
                self.backend = "google"
            else:
                self.backend = "fallback"
        else:
            self.backend = "fallback"

        print(f"    LLM Backend: {self.backend.upper()}")

        if self.backend == "google":
            key = api_key or os.environ.get("GOOGLE_API_KEY", "")
            from google import genai as _genai
            from google.genai import types as _types
            self._genai = _genai
            self._types = _types
            self._client = _genai.Client(api_key=key)
            self._google_model = model_name

    def route(self, msg: dict, signals: dict, ctx, media: dict, max_retries: int = 3) -> dict:
        """Route a message using the chosen backend."""
        if self.backend == "ollama":
            return self._route_ollama(msg, signals, ctx, media, max_retries)
        elif self.backend == "google":
            return self._route_google(msg, signals, ctx, media, max_retries)
        else:
            return self._fallback(signals)

    # ------------------------------------------------------------------
    # OLLAMA backend
    # ------------------------------------------------------------------

    def _route_ollama(self, msg, signals, ctx, media, max_retries) -> dict:
        has_image = bool(media.get("image_b64"))
        model = OLLAMA_VISION_MODEL if has_image else OLLAMA_TEXT_MODEL
        prompt = self._build_prompt_text(msg, signals, ctx, media)
        full_prompt = SYSTEM_PROMPT + "\n\n" + _few_shot_text() + "\n\n" + prompt

        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 512},
        }
        if has_image:
            payload["images"] = [media["image_b64"]]

        for attempt in range(max_retries):
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{OLLAMA_HOST}/api/generate",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                text = resp.get("response", "")
                return self._parse_response(text, signals)
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"  [Ollama error]: {e}")
                    return self._fallback(signals)

    # ------------------------------------------------------------------
    # GOOGLE backend
    # ------------------------------------------------------------------

    def _route_google(self, msg, signals, ctx, media, max_retries) -> dict:
        prompt_text = self._build_prompt_text(msg, signals, ctx, media)
        types = self._types

        user_parts = [types.Part.from_text(text=prompt_text)]
        if media.get("image_b64") and media.get("image_mime"):
            import base64
            user_parts.append(
                types.Part.from_bytes(
                    data=base64.b64decode(media["image_b64"]),
                    mime_type=media["image_mime"],
                )
            )

        history_contents = []
        for turn in FEW_SHOT_EXAMPLES:
            history_contents.append(
                types.Content(
                    role=turn["role"],
                    parts=[types.Part.from_text(text=turn["parts"][0])],
                )
            )
        history_contents.append(types.Content(role="user", parts=user_parts))

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            max_output_tokens=512,
            response_mime_type="application/json",
        )

        for attempt in range(max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self._google_model,
                    contents=history_contents,
                    config=config,
                )
                return self._parse_response(response.text.strip(), signals)
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"  [Google error]: {e}")
                    return self._fallback(signals)

    # ------------------------------------------------------------------
    # Shared prompt builder
    # ------------------------------------------------------------------

    def _build_prompt_text(self, msg, signals, ctx, media) -> str:
        user_id = msg.get("user_id", "")
        group_id = msg.get("group_id", "")
        business_id = msg.get("business_id", "")
        sender_user_id = msg.get("sender_user_id", "")
        conversation_type = msg.get("conversation_type", "")
        forwarded_count = msg.get("forwarded_count", "0")
        message_text = msg.get("message_text", "")
        media_type = msg.get("media_type", "")
        created_at = msg.get("created_at", "")

        lines = [f"## Incoming Message (ID: {msg.get('message_id', '')})"]
        lines += [
            f"- Time: {created_at}",
            f"- Type: {conversation_type}",
            f"- Forwarded count: {forwarded_count}",
        ]
        if message_text:
            lines.append(f"- Text: \"{message_text}\"")
        if media_type:
            lines.append(f"- Media: {media_type}")
            if media.get("voice_note_desc"):
                lines.append(f"  {media['voice_note_desc']}")

        user = ctx.get_user(user_id)
        if user:
            lines += [
                f"\n## User ({user_id})",
                f"- DND window: {user.get('do_not_disturb_window', 'N/A')}",
                f"- Messages opened/30d: {user.get('messages_opened_30d', '?')}",
                f"- Messages replied/30d: {user.get('messages_replied_30d', '?')}",
                f"- Notifications dismissed/30d: {user.get('notifications_dismissed_30d', '?')}",
                f"- Messages reported/30d: {user.get('messages_reported_30d', '?')}",
            ]

        if group_id:
            group = ctx.get_group(group_id)
            membership = ctx.get_group_membership(group_id, user_id)
            if group:
                lines += [
                    f"\n## Group ({group_id}): {group.get('group_name', '')}",
                    f"- Type: {group.get('group_type', '')}, Members: {group.get('member_count', '')}",
                    f"- Admins: {group.get('admin_count', '')}, Messages/30d: {group.get('messages_30d', '')}",
                ]
            if membership:
                lines += [
                    f"- User role: {membership.get('role', '')}",
                    f"- User muted group: {membership.get('group_muted_by_user', '0')}",
                    f"- User dismissals in group: {membership.get('notifications_dismissed_30d', '?')}",
                    f"- User replies in group: {membership.get('replies_sent_30d', '?')}",
                ]

        if business_id:
            biz = ctx.get_business(business_id)
            ubh = ctx.get_user_business_history(user_id, business_id)
            if biz:
                lines += [
                    f"\n## Business ({business_id}): {biz.get('display_name', '')}",
                    f"- Verified: {biz.get('verified', '0')}",
                    f"- Official domain: {biz.get('official_domain', 'N/A')}",
                    f"- Domain used by sender: {biz.get('domain_used_by_sender', 'N/A')}",
                    f"- Domain mismatch: {signals.get('business_domain_mismatch', False)}",
                    f"- Account age (days): {biz.get('account_age_days', '?')}",
                    f"- User reports/30d: {biz.get('user_reports_30d', '?')}",
                    f"- Category: {biz.get('category', '')}",
                ]
            if ubh:
                lines += [
                    f"- User relationship: {ubh.get('why_user_knows_account', 'none')}",
                    f"- User opted out: {bool(ubh.get('promotions_opted_out_at', ''))}",
                    f"- Allows promotions: {ubh.get('allows_promotions', '0')}",
                    f"- Last activity: {ubh.get('last_activity_at', 'N/A')}",
                ]
            else:
                lines.append("- No prior user-business relationship found.")

        lines += [
            f"\n## Pre-computed Signals",
            f"- is_scam_otp: {signals.get('is_scam_otp')}",
            f"- is_chain_forward: {signals.get('is_chain_forward')}",
            f"- is_prompt_injection: {signals.get('is_prompt_injection')}",
            f"- has_urgency: {signals.get('has_urgency')}",
            f"- user_is_mentioned: {signals.get('user_is_mentioned')}",
            f"- group_muted_by_user: {signals.get('group_muted_by_user')}",
            f"- user_opted_out: {signals.get('user_opted_out')}",
        ]

        history = ctx.get_relevant_history(user_id, group_id, business_id, sender_user_id, limit=5)
        if history:
            lines.append(f"\n## Relevant Historical Messages (for evidence)")
            for hist, event in history:
                evt_str = ""
                if event:
                    parts = []
                    if event.get("message_opened") == "1": parts.append("opened")
                    if event.get("message_replied") == "1": parts.append("replied")
                    if event.get("notification_dismissed") == "1": parts.append("dismissed")
                    if event.get("muted_after_message") == "1": parts.append("muted-after")
                    if event.get("message_reported") == "1": parts.append("reported")
                    evt_str = f" [User: {', '.join(parts)}]" if parts else ""
                hist_text = (hist.get("message_text") or "[media]")[:120]
                lines.append(f"- {hist['message_id']} ({hist.get('created_at','')[:10]}): \"{hist_text}\"{evt_str}")

        lines += ["\n## Task", "Based on all the context above, classify this message. Output ONLY a JSON object."]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, text: str, signals: dict) -> dict:
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    return self._fallback(signals)
            else:
                return self._fallback(signals)

        action = str(data.get("action", "digest")).lower()
        message_type = str(data.get("message_type", "unknown")).lower()
        reason = str(data.get("reason", "No reason provided."))
        confidence = float(data.get("confidence", 0.7))
        evidence = str(data.get("evidence_message_ids", "none")).strip()

        if action not in VALID_ACTIONS: action = "digest"
        if message_type not in VALID_TYPES: message_type = "unknown"
        confidence = max(0.0, min(1.0, confidence))
        if not evidence: evidence = "none"

        return {"action": action, "message_type": message_type, "reason": reason,
                "confidence": confidence, "evidence_message_ids": evidence}

    def _fallback(self, signals: dict) -> dict:
        if signals.get("hard_mute"):
            return {"action": "mute", "message_type": signals.get("suggested_type") or "spam",
                    "reason": "Muted by rule-based signal (scam/forward/injection detected).",
                    "confidence": 0.82, "evidence_message_ids": "none"}
        elif signals.get("hard_notify"):
            return {"action": "notify", "message_type": "urgent",
                    "reason": "Notified by rule-based signal (urgency + direct mention).",
                    "confidence": 0.75, "evidence_message_ids": "none"}
        return {"action": "digest", "message_type": "unknown",
                "reason": "Default digest: no LLM available, no hard rule triggered.",
                "confidence": 0.5, "evidence_message_ids": "none"}


def _few_shot_text() -> str:
    """Render few-shot examples as plain text for Ollama prompt."""
    parts = []
    for i in range(0, len(FEW_SHOT_EXAMPLES) - 1, 2):
        u = FEW_SHOT_EXAMPLES[i]
        a = FEW_SHOT_EXAMPLES[i + 1] if i + 1 < len(FEW_SHOT_EXAMPLES) else None
        parts.append(f"User: {u['parts'][0]}")
        if a:
            parts.append(f"Assistant: {a['parts'][0]}")
    return "\n".join(parts)
