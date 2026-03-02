"""
QR code generation utility for PIX charges.

Generates QR code PNG images from PIX "copia e cola" payloads
and attaches them to Frappe documents.
"""

import io

import frappe


def generate_qrcode_for_doc(doc):
    """Generate a QR code image and attach to a document.

    Args:
        doc: Frappe document with 'pix_copia_cola' and 'qrcode_image' fields.
    """
    pix_payload = doc.get("pix_copia_cola")
    if not pix_payload:
        return

    try:
        import qrcode
        from PIL import Image
    except ImportError:
        frappe.log_error(
            "qrcode or Pillow not installed. Run: pip install 'qrcode[pil]'",
            "QR Code Generation Error",
        )
        return

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(pix_payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    # Save as Frappe File
    file_name = f"qrcode_{doc.name}.png"
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name,
        "content": buffer.read(),
        "is_private": 0,  # QR codes need to be accessible
    })
    file_doc.save(ignore_permissions=True)

    # Update document
    doc.qrcode_image = file_doc.file_url
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return file_doc.file_url
