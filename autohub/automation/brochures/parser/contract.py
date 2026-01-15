from dataclasses import dataclass
from typing import List, Any

@dataclass
class ParsedBlock:
    type: str
    content: Any

@dataclass
class ParsedDocument:
    
    source: str
    blocks: List[ParsedBlock]