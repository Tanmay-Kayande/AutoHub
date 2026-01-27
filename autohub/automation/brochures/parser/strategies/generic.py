from autohub.automation.brochures.parser.contract import ParsedDocument, ParsedBlock
from docling.document_converter import DocumentConverter, PdfFormatOption, FormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat


class GenericDoclingParser:
    def __init__(self):
        pdf_pipeline_options = PdfPipelineOptions(
            do_ocr=False
        )
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
