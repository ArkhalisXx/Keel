"""Bounded, page-at-a-time PDF rendering and local Tesseract recognition."""
import math
import os

import pytesseract
from django.conf import settings

from .services import DocumentExtractionError


class PDFOCR:
    def __init__(self, file):
        import pypdfium2
        file.seek(0)
        self.pdf = pypdfium2.PdfDocument(file)

    def text(self, index):
        if settings.TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
        os.environ.setdefault('OMP_THREAD_LIMIT', '1')
        page = self.pdf[index]
        bitmap = image = None
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                raise DocumentExtractionError('A PDF page has invalid dimensions. Export a new PDF copy.')
            scale = min(settings.OCR_DPI / 72, math.sqrt(settings.OCR_MAX_PIXELS / (width * height)))
            # Leave rounding room so the rendered bitmap stays below the pixel budget.
            scale *= 0.99
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            return pytesseract.image_to_string(
                image, lang=settings.OCR_LANGUAGE, config='--psm 3', timeout=settings.OCR_PAGE_TIMEOUT)
        except pytesseract.TesseractNotFoundError:
            raise DocumentExtractionError('OCR could not start: Tesseract is missing. Start the Docker worker or install Tesseract and configure TESSERACT_CMD.') from None
        except pytesseract.TesseractError:
            raise DocumentExtractionError('Tesseract could not read a page. Check that the configured OCR language is installed, then retry.') from None
        except RuntimeError:
            raise DocumentExtractionError('OCR timed out on a page. Split the PDF, reduce OCR_DPI, or increase OCR_PAGE_TIMEOUT and retry.') from None
        finally:
            if image is not None:
                image.close()
            if bitmap is not None:
                bitmap.close()
            page.close()

    def close(self):
        self.pdf.close()
