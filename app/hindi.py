"""Devanagari display helpers. The app shows Hindi in Devanagari (not Roman Hinglish), while still
understanding Hinglish input."""
from __future__ import annotations

from datetime import date

WEEKDAY_DEV = {"Monday": "सोमवार", "Tuesday": "मंगलवार", "Wednesday": "बुधवार", "Thursday": "गुरुवार",
               "Friday": "शुक्रवार", "Saturday": "शनिवार", "Sunday": "रविवार"}
WEEKDAY_SHORT = {"Monday": "सोम", "Tuesday": "मंगल", "Wednesday": "बुध", "Thursday": "गुरु",
                 "Friday": "शुक्र", "Saturday": "शनि", "Sunday": "रवि"}
MONTHS_DEV = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
CATEGORY_DEV = {"groceries": "किराना", "snacks": "स्नैक्स", "dairy": "डेयरी", "household": "घरेलू सामान"}

# Devanagari names for the synthetic demo data (new customers added by voice keep what was spoken).
DEMO_NAMES_HI = {
    "Suresh": "सुरेश", "Anita Desai": "अनीता देसाई", "Vikram Joshi": "विक्रम जोशी", "Kavita Shinde": "कविता शिंदे",
    "Meena Patil": "मीना पाटिल", "Rajesh Gupta": "राजेश गुप्ता", "Sunita Yadav": "सुनीता यादव",
    "Amit Verma": "अमित वर्मा", "Deepak Mehta": "दीपक मेहता", "Farhan Shaikh": "फ़रहान शेख", "Mohan Chawla": "मोहन चावला",
}
DEMO_STORE_HI = {"store_name_hi": "शर्मा जनरल स्टोर", "owner_name_hi": "रमेश"}


def has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text or "")


def hi_date(d: date) -> str:
    return f"{d.day} {MONTHS_DEV[d.month - 1]}"


def hi_name(record: dict, key: str = "name") -> str:
    """Devanagari display name if known, else the stored name."""
    return record.get(f"{key}_hi") or record.get("name_hi") or record.get(key) or ""


def first(name: str) -> str:
    return (name or "").split()[0] if name else ""
