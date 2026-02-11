import re
from typing import Optional

def extract_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    
    text = text.replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None

def clean_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    
    return " ".join(text.strip().split())
