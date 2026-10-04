"""
signal_extractor.py
Rule-based pre-filtering that extracts structured signals from a message
before sending it to the LLM. Catches clear-cut scam, spam, and chain-forward
patterns without burning API quota.
"""

import re

# ---------------------------------------------------------------------------
# Keyword patterns
# ---------------------------------------------------------------------------

SCAM_OTP_PATTERNS = re.compile(
    r"\b(otp|one.?time.?pass|account.?block|profile.?block|account.?lock|"
    r"verify.?now|verification.?code|login.?code|confirm.?otp|"
    r"wallet.?pin|account-login|account-help|pay-check|chase-secure|"
    r"amazonpay-delivery|bit\.ly|account\.in/|profile.?restrict)\b",
    re.IGNORECASE,
)

CHAIN_FORWARD_PATTERNS = re.compile(
    r"\b(forward.{0,20}(ten|10|people|everyone|all.?groups)|"
    r"share.{0,20}(10|ten|everyone|all.?groups|family.?group)|"
    r"do.?not.?break.?the.?chain|send.?to.?10|good.?luck.?chain|"
    r"blessings.{0,30}share|positive.?energy.{0,30}forward|"
    r"share.{0,20}blessing|forward.{0,20}luck|"
    r"matlab.{0,30}sab.?group)\b",
    re.IGNORECASE,
)

PROMPT_INJECTION_PATTERNS = re.compile(
    r"\b(ignore.{0,30}(previous|routing|rules?|instruction)|"
    r"routing.?override|set.?action=|mark.{0,10}notify|"
    r"internal.?router|verified_business=true|user_priority=high|"
    r"system.?note.{0,20}(router|notification)|"
    r"assistant.?instruction)\b",
    re.IGNORECASE,
)

URGENCY_KEYWORDS = re.compile(
    r"\b(urgent|urgently|asap|immediately|right.?now|"
    r"can.?you.?come.?online|call.?me.?now|"
    r"escalation|threshold|deadline|eod|tonight|"
    r"last.?minute|aaj|jaldi|abhi)\b",
    re.IGNORECASE,
)

PERSONAL_DIRECT_KEYWORDS = re.compile(
    r"\b(call me|message me|let me know|tell me|confirm|"
    r"can you|could you|please reply|pls reply|pls confirm|"
    r"need you|need your|mujhe bata|bata do)\b",
    re.IGNORECASE,
)

SCAM_LINK_PATTERNS = re.compile(
    r"(https?://[^\s]*\.(in|com|net|org)/[^\s]*"
    r"|[a-z0-9-]+\.(in|xyz|club|online)/[^\s]*)",
    re.IGNORECASE,
)

GREETING_PATTERNS = re.compile(
    r"^(good morning|good evening|good night|stay blessed|"
    r"smile today|bhagwan|subah|shubh|have a good day|"
    r"positive energy|god bless|stay positive)[^.!?]*[.!]?\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def extract_signals(msg: dict, ctx) -> dict:
    """
    Returns a structured dict of signals for this message.

    Keys:
      - is_scam_otp (bool)
      - is_chain_forward (bool)
      - is_prompt_injection (bool)
      - is_greeting_only (bool)
      - has_urgency (bool)
      - has_direct_personal (bool)
      - is_forwarded_heavily (bool): forwarded_count >= 6
      - business_verified (bool | None)
      - business_domain_mismatch (bool)
      - business_high_reports (bool)
      - user_opted_out (bool)
      - user_dismissed_heavily (bool)
      - group_muted_by_user (bool)
      - user_is_mentioned (bool)
      - has_active_business_relationship (bool)
      - hard_mute (bool): clear-cut mute signal
      - hard_notify (bool): clear-cut notify signal
      - suggested_type (str | None)
    """
    text = (msg.get("message_text") or "").strip()
    user_id = msg.get("user_id", "")
    group_id = msg.get("group_id", "")
    business_id = msg.get("business_id", "")
    sender_user_id = msg.get("sender_user_id", "")
    forwarded_count = _int(msg.get("forwarded_count", 0))
    conversation_type = msg.get("conversation_type", "")
    media_type = msg.get("media_type", "")

    signals: dict = {}

    # --- Text pattern signals ---
    signals["is_scam_otp"] = bool(SCAM_OTP_PATTERNS.search(text))
    signals["is_chain_forward"] = bool(CHAIN_FORWARD_PATTERNS.search(text))
    signals["is_prompt_injection"] = bool(PROMPT_INJECTION_PATTERNS.search(text))
    signals["is_greeting_only"] = bool(GREETING_PATTERNS.match(text)) and len(text) < 200
    signals["has_urgency"] = bool(URGENCY_KEYWORDS.search(text))
    signals["has_direct_personal"] = bool(PERSONAL_DIRECT_KEYWORDS.search(text))
    signals["is_forwarded_heavily"] = forwarded_count >= 6
    signals["user_is_mentioned"] = (f"@{user_id}" in text)

    # --- Business signals ---
    biz = ctx.get_business(business_id) if business_id else {}
    if biz:
        verified = biz.get("verified", "0") == "1"
        official_domain = biz.get("official_domain", "").strip()
        domain_used = biz.get("domain_used_by_sender", "").strip()
        reports = _int(biz.get("user_reports_30d", 0))
        domain_age = _int(biz.get("domain_used_by_sender_age_days", 9999))

        signals["business_verified"] = verified
        signals["business_domain_mismatch"] = bool(
            official_domain and domain_used and official_domain != domain_used
        )
        signals["business_high_reports"] = reports >= 20
        signals["business_new_domain"] = domain_age < 90
    else:
        signals["business_verified"] = None
        signals["business_domain_mismatch"] = False
        signals["business_high_reports"] = False
        signals["business_new_domain"] = False

    # --- User-business relationship ---
    ubh = ctx.get_user_business_history(user_id, business_id) if business_id else {}
    if ubh:
        opted_out = ubh.get("promotions_opted_out_at", "").strip()
        allows_promo = ubh.get("allows_promotions", "0") == "1"
        signals["user_opted_out"] = bool(opted_out)
        signals["user_allows_promotions"] = allows_promo
        signals["has_active_business_relationship"] = bool(ubh.get("why_user_knows_account", ""))
    else:
        signals["user_opted_out"] = False
        signals["user_allows_promotions"] = False
        signals["has_active_business_relationship"] = False

    # --- User engagement signals ---
    user = ctx.get_user(user_id)
    dismissed = _int(user.get("notifications_dismissed_30d", 0))
    reported = _int(user.get("messages_reported_30d", 0))
    signals["user_dismissed_heavily"] = dismissed > 50

    # --- Group signals ---
    membership = ctx.get_group_membership(group_id, user_id) if group_id else {}
    signals["group_muted_by_user"] = membership.get("group_muted_by_user", "0") == "1"

    # --- Hard-rule overrides ---
    hard_mute = False
    hard_notify = False
    suggested_type = None

    if signals["is_prompt_injection"]:
        hard_mute = True
        suggested_type = "scam"

    elif signals["is_scam_otp"] and (
        signals["business_domain_mismatch"]
        or signals["business_new_domain"]
        or signals["business_high_reports"]
        or conversation_type in ("group", "personal")
    ):
        hard_mute = True
        suggested_type = "scam"

    elif signals["is_chain_forward"] and signals["is_forwarded_heavily"]:
        hard_mute = True
        suggested_type = "forward"

    elif (
        signals["is_forwarded_heavily"]
        and signals["is_greeting_only"]
    ):
        hard_mute = True
        suggested_type = "greeting"

    elif (
        business_id
        and not signals["business_verified"]
        and signals["business_domain_mismatch"]
        and signals["is_scam_otp"]
    ):
        hard_mute = True
        suggested_type = "scam"

    # Hard notify: direct personal mention in group, or urgent personal message
    if not hard_mute:
        if signals["user_is_mentioned"] and signals["has_urgency"]:
            hard_notify = True
        elif conversation_type == "personal" and signals["has_urgency"] and not signals["is_scam_otp"]:
            hard_notify = True

    signals["hard_mute"] = hard_mute
    signals["hard_notify"] = hard_notify
    signals["suggested_type"] = suggested_type

    return signals


def _int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
