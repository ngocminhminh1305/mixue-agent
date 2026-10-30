"""Gemini sales-image extraction and provider status endpoints."""
import base64
import io
import json
import os
import re
from collections import Counter
from pathlib import Path

import httpx
from dotenv import dotenv_values
from fastapi import APIRouter, File, HTTPException, UploadFile
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, UnidentifiedImageError

from bobaops.core.paths import ROOT

ASSET_DIR = Path(__file__).resolve().parent
SCHEMA = json.loads((ASSET_DIR / 'report.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
PROMPT = (ASSET_DIR / 'prompt.md').read_text()
MAX_BYTES = 10 * 1024 * 1024
router = APIRouter()


def settings():
    # Re-read .env so adding the key does not require a server restart.
    values = dotenv_values(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY") or values.get("GEMINI_API_KEY") or ""
    model = os.environ.get("GEMINI_MODEL") or values.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    return key.strip(), model.strip()


def validate_report(report):
    if list(VALIDATOR.iter_errors(report)):
        raise ValueError("The model returned invalid report data. Try another clearer photo.")
    names = Counter(item["item_name"].strip().casefold() for item in report["items"] if item["item_name"])
    for item in report["items"]:
        if not item["item_name"] or not item["item_name"].strip():
            item["issues"].append("Item name needs review.")
        elif names[item["item_name"].strip().casefold()] > 1:
            item["issues"].append("Duplicate item name; review before importing.")
        if item["quantity"] is None:
            item["issues"].append("Quantity needs review.")
    if report["date"] is None:
        report["issues"].append("Report date needs review.")
    if not report["items"]:
        report["issues"].append("No product rows were extracted.")
    return report


def provider_error(response, key):
    """Expose actionable API errors without exposing credentials or payloads."""
    messages = {
        400: "Gemini rejected the request.",
        401: "Gemini rejected the API key. Check .env.",
        403: "Gemini access was denied. Check the key and project permissions.",
        404: "The configured model is unavailable. Check GEMINI_MODEL in .env.",
        429: "Gemini's quota is exhausted. Check your project's free-tier quota in AI Studio; no paid fallback was used.",
    }
    message = messages.get(response.status_code, "Gemini is unavailable. Try later.")
    # Request/model errors often explain an unsupported field or model name.
    # Never expose complete provider responses, request bodies, or URLs.
    if response.status_code in {400, 404}:
        try:
            detail = response.json().get("error", {}).get("message", "")
            if isinstance(detail, str):
                detail = detail.replace(key, "[redacted]") if key else detail
                detail = re.sub(r"AIza[\w-]+", "[redacted]", detail)
                detail = re.sub(r"https?://\S+", "[provider link]", detail)
                message += " " + detail[:600]
        except (ValueError, AttributeError):
            pass
    return f"Gemini HTTP {response.status_code}: {message}"


@router.get("/api/status")
def status():
    key, model = settings()
    return {"key_configured": bool(key), "model": model}


@router.post("/api/extract")
async def extract(image: UploadFile = File(...)):
    key, model = settings()
    if not key:
        raise HTTPException(400, "Add GEMINI_API_KEY to the project's .env file first.")
    data = await image.read(MAX_BYTES + 1)
    await image.close()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "Choose an image smaller than 10 MB.")
    try:
        with Image.open(io.BytesIO(data)) as photo:
            if photo.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(400, "Supported images: JPG, PNG, and WebP.")
            mime = Image.MIME[photo.format]
            photo.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "This file is not a readable image.") from None
    # One explicit request per click: no automatic retries or model fallbacks.
    schema = {k: v for k, v in SCHEMA.items() if k != "$schema"}
    payload = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema, "maxOutputTokens": 8192},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key}, json=payload,
            )
    except httpx.ConnectTimeout:
        raise HTTPException(504, "Could not establish a connection to Gemini within 20 seconds. Check your network or VPN; this is a connection timeout.") from None
    except httpx.ReadTimeout:
        raise HTTPException(504, "Gemini did not respond within 180 seconds. Try the same photo in Google AI Studio to check whether the delay also happens there.") from None
    except httpx.TimeoutException:
        raise HTTPException(504, "The photo upload to Gemini timed out. Check your connection and try again manually.") from None
    except httpx.RequestError:
        raise HTTPException(502, "Could not connect to Gemini. Check your internet connection.") from None
    if response.status_code != 200:
        raise HTTPException(502, provider_error(response, key))
    try:
        parts = response.json()["candidates"][0]["content"]["parts"]
        raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        report = validate_report(json.loads(raw))
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Gemini did not return a valid report. Try a clearer photo.") from None
    return report
