"""Conservative local receipt extraction. Suggestions are never verified automatically."""
import io
import re
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
import pytesseract

MAX_BYTES=10*1024*1024
FIELDS=('order_id','merchant','product','purchase_date','delivery_date','amount','currency','payment_reference')

def extract_text(text):
    patterns={
        'order_id':r'(?:order\s*(?:id|number|no|#)?)[\s:#-]+([A-Za-z0-9][A-Za-z0-9-]{2,39})',
        'merchant':r'(?:merchant|sold by|store)\s*[:#]\s*([^\n]{2,100})',
        'product':r'(?:product|item)\s*:\s*([^\n]{2,160})',
        'purchase_date':r'(?:purchase date|order date|date)\s*:\s*(\d{4}-\d{2}-\d{2})',
        'delivery_date':r'(?:delivery date|delivered)\s*:\s*(\d{4}-\d{2}-\d{2})',
        'amount':r'(?:total|paid|amount)\s*:?\s*(?:INR|USD|EUR|GBP|Rs\.?|₹|\$)?\s*([\d,]+(?:\.\d{1,2})?)',
        'currency':r'\b(INR|USD|EUR|GBP)\b',
        'payment_reference':r'(?:payment reference|transaction id|utr)\s*:\s*([A-Za-z0-9-]{3,80})'
    }
    result={}
    for field in FIELDS:
        m=re.search(patterns[field],text,re.I)
        result[field]={'value':m.group(1).strip() if m else None,'confidence':0.85 if m else 0.0,'source':m.group(0) if m else None,'requires_confirmation':True}
    return {'fields':result,'text':text[:30000],'verified':False,'method':'deterministic labeled-field extraction'}

def extract_file(name, data):
    if not data or len(data)>MAX_BYTES: raise ValueError('Upload must be nonempty and at most 10 MB')
    suffix=Path(name or '').suffix.lower()
    if suffix not in ('.pdf','.png','.jpg','.jpeg'): raise ValueError('Only PDF, PNG and JPEG are supported')
    warning=None
    try:
        if suffix=='.pdf':
            if not data.startswith(b'%PDF-'): raise ValueError('File content is not PDF')
            reader=PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages)>20: raise ValueError('Use an unencrypted PDF of at most 20 pages')
            text='\n'.join((p.extract_text() or '') for p in reader.pages)
            mime='application/pdf'
            if not text.strip(): warning='Scanned PDF has no text layer. Upload an image for OCR or enter fields manually.'
        else:
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in ('JPEG','PNG') or im.width*im.height>20000000: raise ValueError('Invalid or oversized image')
                if (suffix=='.png') != (im.format=='PNG'): raise ValueError('File extension does not match image content')
                im.load()
                mime='image/png' if im.format=='PNG' else 'image/jpeg'
                try: text=pytesseract.image_to_string(im,timeout=20)
                except (pytesseract.TesseractNotFoundError,RuntimeError):
                    text='';warning='OCR engine unavailable or timed out. Enter receipt fields manually; the document is stored for review.'
    except (ValueError,UnidentifiedImageError,Image.DecompressionBombError) as e: raise ValueError(str(e)) from None
    except Exception: raise ValueError('Unable to read this document. Upload a valid receipt.') from None
    return {**extract_text(text),'warning':warning},mime,suffix
