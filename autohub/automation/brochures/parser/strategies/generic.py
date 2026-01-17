from autohub.automation.brochures.parser.contract import ParsedDocument, ParsedBlock
from docling.document_converter import DocumentConverter, PdfFormatOption, FormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat

class GenericDoclingParser:
    def __init__(self):
        # Disable OCR for fast development
        pdf_pipeline_options = PdfPipelineOptions(do_ocr=False)

        format_options: dict[InputFormat, FormatOption] = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pdf_pipeline_options
            )
        }

        self.converter = DocumentConverter(format_options=format_options)

    def parse(self, file_path: str) -> ParsedDocument:
        result = self.converter.convert(file_path)

        doc = result.document
        if doc is None:
            raise RuntimeError(f"Docling produced no document for {file_path}")

        data = doc.export_to_dict()

        blocks: list[ParsedBlock] = []

        # ✅ PRIMARY TEXT SOURCE (Docling structured output)
        texts = data.get("texts", [])

        if isinstance(texts, list):
            for item in texts:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text and text.strip():
                        blocks.append(
                            ParsedBlock(
                                type="text",
                                content=text.strip(),
                            )
                        )

        # ✅ FALLBACK: tables (optional, future use)
        tables = data.get("tables", [])
        if isinstance(tables, list):
            for table in tables:
                blocks.append(
                    ParsedBlock(
                        type="table",
                        content=table,
                    )
                )

        if not blocks:
            raise RuntimeError(
                f"Docling returned no usable text blocks. Keys: {list(data.keys())}"
            )

        return ParsedDocument(
            source=file_path,
            blocks=blocks,
        )










"""
from autohub.automation.brochures.parser.contract import ParsedDocument, ParsedBlock
from docling.document_converter import DocumentConverter, PdfFormatOption, FormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat


class GenericDoclingParser:
    def __init__(self):
        # Disable OCR for fast development
        pdf_pipeline_options = PdfPipelineOptions(do_ocr=False)

        format_options: dict[InputFormat, FormatOption] = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pdf_pipeline_options
            )
        }

        self.converter = DocumentConverter(format_options=format_options)

    def parse(self, file_path: str) -> ParsedDocument:
        result = self.converter.convert(file_path)

        doc = result.document
        if doc is None:
            raise RuntimeError(f"Docling produced no document for {file_path}")

        data = doc.export_to_dict()

        blocks: list[ParsedBlock] = []

        # Docling 2.68.0 returns pages as list[str] when OCR is disabled
        pages = data.get("pages", [])

        if not isinstance(pages, list):
            raise RuntimeError(
                f"Unexpected Docling export format. Keys: {list(data.keys())}"
            )

        for page_text in pages:
            if isinstance(page_text, str) and page_text.strip():
                blocks.append(
                    ParsedBlock(
                        type="text",
                        content=page_text.strip(),
                    )
                )

        return ParsedDocument(
            source=file_path,
            blocks=blocks,
        )
"""






"""from docling.document_converter import DocumentConverter
from typing import Any, cast
# docking is a library for parsing documents into structured data it is a better alternative to docx2txt and pdfminer

from ..base import BaseParser
from ..contract import ParsedBlock, ParsedDocument

class GenericDoclingParser(BaseParser):
    """"""
    Defalt parser using Docling for most document
    """"""

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
"""