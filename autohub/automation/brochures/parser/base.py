from abc import ABC, abstractmethod
from .contract import ParsedDocument

class BaseParser(ABC):

    @abstractmethod
    def parse(self, file_path: str) -> ParsedDocument:
        """Take fine and return ParsedDocument"""
        pass
