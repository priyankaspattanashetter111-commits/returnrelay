"""Create a clearly fictional text-layer PDF for deterministic upload demos."""
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

def sample_bytes():
    import io
    w=PdfWriter();p=w.add_blank_page(width=595,height=842)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
    lines=['RETURNRELAY - FICTIONAL DEMONSTRATION RECEIPT','Not a real purchase or financial document.','Order ID: UPLOAD-2026','Merchant: Fictional Paper Store','Product: Campus notebook','Purchase date: 2026-11-20','Delivery date: 2026-11-23','Total: INR 499.00','Payment reference: DEMO-PAY-2026','Support: returns@paper.example']
    stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 48 780 Td 25 TL '+' '.join('('+line+') Tj T*' for line in lines)+' ET').encode())
    p[NameObject('/Contents')]=w._add_object(stream)
    b=io.BytesIO();w.write(b);return b.getvalue()

if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'samples/fictional-receipt.pdf'
    target.parent.mkdir(exist_ok=True);target.write_bytes(sample_bytes());print(target)
