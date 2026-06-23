"""SHIMS Document Engine — unified branded PDF generation with template editing."""
from .branded_base import BrandedPDF, DocumentLine, DocumentSection, FormatConfig
from .template_manager import DocumentTemplateManager
from .coa_engine import COAEngine
from .preview_engine import PreviewEngine
from .regulatory_coa import COADocument, COATestRow, coa_from_fields, render_coa
from .rich_docx import (
    DocStyleProfile, TextStyle, PROFILES, build_docx, available_profiles,
)

__all__ = ['BrandedPDF', 'DocumentLine', 'DocumentSection', 'FormatConfig', 'DocumentTemplateManager', 'COAEngine', 'PreviewEngine', 'COADocument', 'COATestRow', 'coa_from_fields', 'render_coa', 'DocStyleProfile', 'TextStyle', 'PROFILES', 'build_docx', 'available_profiles']
