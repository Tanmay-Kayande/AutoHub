"""
Selecting appropriate parsers for brochures based on their content or source.
"""

from .strategies.generic import GenericDoclingParser
from base import BaseParser

def get_parser(**kwargs) -> BaseParser:
    
    # Future logic can be added here to select different parsers
    return GenericDoclingParser()