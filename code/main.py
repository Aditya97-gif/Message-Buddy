"""
main.py — Message Notification Router
Entry point for the HackerRank Orchestrate challenge.

Usage:
    export GOOGLE_API_KEY=your_key_here
    python code/main.py

Output: dataset/output.csv
"""

import os
import csv
import sys
import time

# Allow running from repo root or from code/ directory
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_HERE, "..", ".env"))
    load_dotenv(os.path.join(_HERE, ".env"))
except ImportError:
    pass

from context_loader import DataContext
from signal_extractor import extract_signals
from media_handler import prepare_media_for_gemini
from llm_router import LLMRouter


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
DATASET_DIR = os.path.join(REPO_ROOT, "dataset")
INPUT_CSV = os.path.join(DATASET_DIR, "messages.csv")
OUTPUT_CSV = os.path.join(DATASET_DIR, "output.csv")

OUTPUT_COLUMNS = [
    "message_id",
    "action",
    "message_type",
    "reason",
    "confidence",
    "evidence_message_ids",
]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    backend = os.environ.get("LLM_BACKEND", "auto")

    if not api_key and backend in ("google",):
        print("ERROR: LLM_BACKEND=google but GOOGLE_API_KEY not set.")
        print("  Set it with: export GOOGLE_API_KEY=your_key_here")
        sys.exit(1)

    if not api_key and backend == "auto":
        print("[!] GOOGLE_API_KEY not set — will try Ollama (local). Set key for Google fallback.")

    print(f"[+] Loading dataset from {DATASET_DIR}")
    ctx = DataContext(DATASET_DIR)
    print(f"    Messages to route: {len(ctx.messages)}")
    print(f"    Historical messages: {len(ctx.message_history)}")
    print(f"    Users: {len(ctx.users)}")

    print("[+] Initialising LLM router...")
    router = LLMRouter(api_key=api_key)

    results = []
    total = len(ctx.messages)

    for i, msg in enumerate(ctx.messages):
        msg_id = msg.get("message_id", f"row_{i}")
        print(f"  [{i+1}/{total}] {msg_id} ...", end=" ", flush=True)

        # 1. Extract rule-based signals
        signals = extract_signals(msg, ctx)

        # 2. Prepare media (image b64, voice note description)
        media = prepare_media_for_gemini(msg, ctx)

        # 3. Hard-rule short-circuit to save quota
        if signals["hard_mute"] and not media.get("image_b64"):
            result = {
                "action": "mute",
                "message_type": signals.get("suggested_type") or "spam",
                "reason": _hard_mute_reason(signals),
                "confidence": 0.88,
                "evidence_message_ids": _find_evidence(msg, ctx),
            }
            print(f"-> HARD MUTE ({result['message_type']})")
        else:
            # 4. Full LLM routing
            result = router.route(msg, signals, ctx, media)
            # Respect hard signals: if hard_mute but LLM said notify/digest, override
            if signals["hard_mute"] and result["action"] != "mute":
                result["action"] = "mute"
                result["message_type"] = signals.get("suggested_type") or result["message_type"]
                result["confidence"] = max(result["confidence"], 0.82)
                result["reason"] = _hard_mute_reason(signals) + " " + result["reason"]
            print(f"-> {result['action'].upper()} / {result['message_type']}")

        results.append({
            "message_id": msg_id,
            **result,
        })

        # Polite rate limiting: ~60 requests/min for free tier
        if (i + 1) % 15 == 0:
            time.sleep(1)

    # ---------------------------------------------------------------------------
    # Write output.csv
    # ---------------------------------------------------------------------------
    print(f"\n[+] Writing {len(results)} predictions to {OUTPUT_CSV}")

    # Preserve the original row order from messages.csv
    msg_order = [m["message_id"] for m in ctx.messages]
    results_by_id = {r["message_id"]: r for r in results}
    ordered_results = [results_by_id[mid] for mid in msg_order if mid in results_by_id]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered_results)

    print(f"[+] Done! output.csv has {len(ordered_results)} rows.")

    # ---------------------------------------------------------------------------
    # Quick validation
    # ---------------------------------------------------------------------------
    _validate(ordered_results, ctx)


def _hard_mute_reason(signals: dict) -> str:
    if signals.get("is_prompt_injection"):
        return "Message attempts to override routing instructions (prompt injection) — classified as scam."
    if signals.get("is_scam_otp"):
        return "Message requests OTP, password, or account verification through suspicious pressure or link."
    if signals.get("is_chain_forward") and signals.get("is_forwarded_heavily"):
        return "Chain-forward message with high forward count. User pattern suggests dismissal."
    if signals.get("is_greeting_only") and signals.get("is_forwarded_heavily"):
        return "Forwarded greeting message with very high forward count. Low value to user."
    return "Muted by rule-based signal (clear scam, spam, or chain-forward pattern)."


def _find_evidence(msg: dict, ctx) -> str:
    """Find up to 2 historical message IDs for hard-rule mutes."""
    user_id = msg.get("user_id", "")
    group_id = msg.get("group_id", "")
    business_id = msg.get("business_id", "")
    sender_user_id = msg.get("sender_user_id", "")
    history = ctx.get_relevant_history(user_id, group_id, business_id, sender_user_id, limit=3)
    ids = [h["message_id"] for h, _ in history[:2]]
    return ";".join(ids) if ids else "none"


def _validate(results: list, ctx):
    """Basic sanity checks on the output."""
    from llm_router import VALID_ACTIONS, VALID_TYPES
    issues = []
    expected_ids = {m["message_id"] for m in ctx.messages}
    result_ids = {r["message_id"] for r in results}

    missing = expected_ids - result_ids
    extra = result_ids - expected_ids
    if missing:
        issues.append(f"Missing predictions for: {missing}")
    if extra:
        issues.append(f"Extra predictions (not in messages.csv): {extra}")

    for r in results:
        if r["action"] not in VALID_ACTIONS:
            issues.append(f"{r['message_id']}: invalid action '{r['action']}'")
        if r["message_type"] not in VALID_TYPES:
            issues.append(f"{r['message_id']}: invalid message_type '{r['message_type']}'")

    if issues:
        print("\n[!] Validation issues:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("[OK] Validation passed: all message_ids present, all enum values valid.")


if __name__ == "__main__":
    main()
