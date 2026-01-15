from docling.document_converter import DocumentConverter
from typing import Any, cast
# docking is a library for parsing documents into structured data it is a better alternative to docx2txt and pdfminer

from ..base import BaseParser
from ..contract import ParsedBlock, ParsedDocument

class GenericDoclingParser(BaseParser):
    """
    Defalt parser using Docling for most document
    """

    def __init__(self):
        # Initialize docling document converter
        self.converter = DocumentConverter()

    def parse(self, file_path: str) -> ParsedDocument:
        result = self.converter.convert(file_path)

        blocks: list[ParsedBlock] = []
        document = cast(Any, result.document)

        for element in document.elements:
            element_type = element.type.lower()
            if element_type == "title":
                blocks.append(ParsedBlock("title", element.text.strip()))
            elif element_type == "paragraph":
                blocks.append(ParsedBlock("paragraph", element.text.strip()))

            elif element_type == "table":
                blocks.append(ParsedBlock("table", element.to_dict()))

        return ParsedDocument(
            source = file_path,
            blocks = blocks
        )