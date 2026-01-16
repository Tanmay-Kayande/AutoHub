"""
Extract Car information from parsed documents.
"""
from abc import ABC, abstractmethod
from autohub.model.schemas import Car

class BaseCarExtractor(ABC):
    @abstractmethod
    def extract(self, parsed_doc) -> Car:
        pass

