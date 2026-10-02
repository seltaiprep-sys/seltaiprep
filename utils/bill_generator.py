"""
Bill / Invoice Generator
========================

Generates professional PDF invoices for automatic (eSewa) payments.
Uses reportlab (already in requirements.txt).

Public API:
    generate_bill_pdf(bill_data: dict) -> str
        Returns file path of generated PDF.

    get_bill_filename(bill_number: str) -> str
        Returns safe filename.
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
BILLS_DIR = Path('protected_uploads/bills')
BILLS_DIR.mkdir(parents=True, exist_ok=True)

# Brand colors (match app theme)
BRAND_PURPLE = colors.HexColor('#6c63ff')
BRAND_DARK = colors.HexColor('#1a2334')
TEXT_DARK = colors.HexColor('#222222')
TEXT_MUTED = colors.HexColor('#666666')
TEXT_LIGHT = colors.HexColor('#999999')
BORDER_GRAY = colors.HexColor('#e0e0e0')
BG_LIGHT = colors.HexColor('#f7f7fb')


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════
def _format_amount(amount: int) -> str:
    """Format amount with thousands separator."""
    try:
        return f"{int(amount):,}"
    except (ValueError, TypeError):
        return str(amount)


def _safe(value, default: str = '—') -> str:
    """Escape HTML-unsafe chars for reportlab Paragraph."""
    if value is None or value == '':
        return default
    s = str(value)
    return (
        s.replace('&', '&amp;')
         .replace('<', '&lt;')
         .replace('>', '&gt;')
    )


def _format_date(dt) -> str:
    """Format datetime for display."""
    if not dt:
        return datetime.now().strftime('%B %d, %Y')
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return dt[:10] if len(dt) >= 10 else dt
    try:
        return dt.strftime('%B %d, %Y')
    except Exception:
        return str(dt)


def _format_datetime(dt) -> str:
    """Format datetime with time."""
    if not dt:
        return datetime.now().strftime('%Y-%m-%d %H:%M')
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return dt[:16] if len(dt) >= 16 else dt
    try:
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(dt)


def get_bill_filename(bill_number: str) -> str:
    """Generate safe filename for bill PDF."""
    safe = ''.join(c for c in bill_number if c.isalnum() or c in '-_.')
    return f"{safe}.pdf"


# ═══════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════
def generate_bill_pdf(bill_data: Dict[str, Any]) -> str:
    """
    Generate a PDF invoice for a payment.

    Args:
        bill_data: Dictionary with:
            - bill_number (str): Unique invoice number (e.g. INV-2026-0001)
            - user_name (str): Customer name
            - user_email (str): Customer email
            - module (str): ielts | pte | ukvi
            - plan (str): plan key (e.g. 30days)
            - plan_label (str): human-readable plan (e.g. "30 Days")
            - amount (int): total in NPR
            - status (str): paid | pending | refunded
            - payment_method (str): esewa | manual | khalti
            - transaction_id (str): payment gateway transaction ID
            - issued_at (datetime): issue timestamp

    Returns:
        File path to generated PDF (relative to project root).

    Raises:
        ValueError: if bill_data is missing required fields.
    """
    # ─── Validate ─────────────────────────────────────────
    if not bill_data.get('bill_number'):
        raise ValueError("bill_number is required")
    if not bill_data.get('module'):
        raise ValueError("module is required")
    if bill_data.get('amount') is None:
        raise ValueError("amount is required")

    bill_number = bill_data['bill_number']
    filename = get_bill_filename(bill_number)
    filepath = BILLS_DIR / filename

    # ─── Setup Document ──────────────────────────────────
    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=f"Invoice {bill_number}",
        author="SELTAI PREP",
        subject="Payment Invoice",
    )

    styles = getSampleStyleSheet()
    story = []

    # ═══════════════════════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════════════════════
    brand_style = ParagraphStyle(
        'Brand',
        parent=styles['Heading1'],
        fontSize=26,
        textColor=BRAND_PURPLE,
        alignment=TA_LEFT,
        spaceAfter=0,
        leading=30,
    )
    brand_sub = ParagraphStyle(
        'BrandSub',
        parent=styles['Normal'],
        fontSize=9,
        textColor=TEXT_MUTED,
        alignment=TA_LEFT,
        spaceAfter=0,
        leading=11,
    )
    invoice_label = ParagraphStyle(
        'InvoiceLabel',
        parent=styles['Heading1'],
        fontSize=22,
        textColor=BRAND_DARK,
        alignment=TA_RIGHT,
        spaceAfter=0,
        leading=26,
    )
    invoice_meta = ParagraphStyle(
        'InvoiceMeta',
        parent=styles['Normal'],
        fontSize=10,
        textColor=TEXT_MUTED,
        alignment=TA_RIGHT,
        spaceAfter=0,
        leading=13,
    )

    # Left column: brand
    left_col = [
        Paragraph("SELTAI PREP", brand_style),
        Spacer(1, 2),
        Paragraph("IELTS · PTE · UKVI Practice Platform", brand_sub),
        Paragraph("www.seltaiprep.com", brand_sub),
    ]

    # Right column: invoice meta
    right_col = [
        Paragraph("INVOICE", invoice_label),
        Spacer(1, 4),
        Paragraph(f"<b>#{_safe(bill_number)}</b>", invoice_meta),
        Paragraph(f"Issued: {_format_date(bill_data.get('issued_at'))}", invoice_meta),
    ]

    header_table = Table(
        [[left_col, right_col]],
        colWidths=[100 * mm, 74 * mm],
        style=TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]),
    )
    story.append(header_table)
    story.append(Spacer(1, 6))

    # ─── Divider ─────────────────────────────────────────
    divider = Table(
        [['']],
        colWidths=[174 * mm],
        rowHeights=[2],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BRAND_PURPLE),
        ]),
    )
    story.append(divider)
    story.append(Spacer(1, 16))

    # ═══════════════════════════════════════════════════════
    # BILL TO + PAYMENT INFO
    # ═══════════════════════════════════════════════════════
    label_style = ParagraphStyle(
        'Label',
        parent=styles['Normal'],
        fontSize=9,
        textColor=TEXT_LIGHT,
        spaceAfter=2,
        leading=11,
    )
    value_style = ParagraphStyle(
        'Value',
        parent=styles['Normal'],
        fontSize=11,
        textColor=TEXT_DARK,
        spaceAfter=0,
        leading=14,
    )
    value_bold = ParagraphStyle(
        'ValueBold',
        parent=value_style,
        fontName='Helvetica-Bold',
    )

    # Left: Bill To
    bill_to_col = [
        Paragraph("BILLED TO", label_style),
        Spacer(1, 4),
        Paragraph(_safe(bill_data.get('user_name'), 'Customer'), value_bold),
        Paragraph(_safe(bill_data.get('user_email'), ''), value_style),
    ]

    # Right: Payment
    payment_method = bill_data.get('payment_method', 'esewa')
    method_labels = {
        'esewa': 'eSewa (Automatic)',
        'manual': 'Bank Transfer (Manual)',
        'khalti': 'Khalti (Automatic)',
    }
    method_label = method_labels.get(payment_method, payment_method.title())

    payment_col = [
        Paragraph("PAYMENT METHOD", label_style),
        Spacer(1, 4),
        Paragraph(_safe(method_label), value_bold),
        Paragraph(f"Date: {_format_date(bill_data.get('issued_at'))}", value_style),
    ]

    info_table = Table(
        [[bill_to_col, payment_col]],
        colWidths=[100 * mm, 74 * mm],
        style=TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]),
    )
    story.append(info_table)
    story.append(Spacer(1, 20))

    # ═══════════════════════════════════════════════════════
    # LINE ITEMS
    # ═══════════════════════════════════════════════════════
    item_header_style = ParagraphStyle(
        'ItemHeader',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.white,
        fontName='Helvetica-Bold',
        leading=13,
    )
    item_text_style = ParagraphStyle(
        'ItemText',
        parent=styles['Normal'],
        fontSize=11,
        textColor=TEXT_DARK,
        leading=14,
    )
    item_amount_style = ParagraphStyle(
        'ItemAmount',
        parent=styles['Normal'],
        fontSize=12,
        textColor=TEXT_DARK,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
        leading=14,
    )

    # Description
    module_upper = (bill_data.get('module') or '').upper()
    plan_label = bill_data.get('plan_label') or bill_data.get('plan') or 'Subscription'
    description = f"{module_upper} Subscription — {plan_label}"
    period_str = ""

    amount = int(bill_data['amount'])
    amount_str = f"NPR {_format_amount(amount)}"

    items_data = [
        [
            Paragraph("DESCRIPTION", item_header_style),
            Paragraph("AMOUNT", ParagraphStyle(
                'AmtHeader',
                parent=item_header_style,
                alignment=TA_RIGHT,
            )),
        ],
        [
            Paragraph(_safe(description), item_text_style),
            Paragraph(amount_str, item_amount_style),
        ],
    ]

    items_table = Table(
        items_data,
        colWidths=[124 * mm, 50 * mm],
        style=TableStyle([
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_PURPLE),
            ('LEFTPADDING', (0, 0), (-1, 0), 12),
            ('RIGHTPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            # Body row
            ('BACKGROUND', (0, 1), (-1, 1), colors.white),
            ('LEFTPADDING', (0, 1), (-1, 1), 12),
            ('RIGHTPADDING', (0, 1), (-1, 1), 12),
            ('TOPPADDING', (0, 1), (-1, 1), 14),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 14),
            # Border
            ('LINEBELOW', (0, 0), (-1, 0), 0, colors.white),
            ('LINEBELOW', (0, 1), (-1, 1), 0.5, BORDER_GRAY),
            ('LINEABOVE', (0, 0), (-1, 0), 0, colors.white),
        ]),
    )
    story.append(items_table)
    story.append(Spacer(1, 4))

    # ═══════════════════════════════════════════════════════
    # TOTALS
    # ═══════════════════════════════════════════════════════
    subtotal_label_style = ParagraphStyle(
        'SubLabel',
        parent=styles['Normal'],
        fontSize=10,
        textColor=TEXT_MUTED,
        alignment=TA_RIGHT,
        leading=14,
    )
    subtotal_value_style = ParagraphStyle(
        'SubValue',
        parent=styles['Normal'],
        fontSize=10,
        textColor=TEXT_DARK,
        alignment=TA_RIGHT,
        leading=14,
    )
    total_label_style = ParagraphStyle(
        'TotLabel',
        parent=styles['Normal'],
        fontSize=13,
        textColor=BRAND_DARK,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
        leading=16,
    )
    total_value_style = ParagraphStyle(
        'TotValue',
        parent=styles['Normal'],
        fontSize=16,
        textColor=BRAND_PURPLE,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
        leading=20,
    )

    totals_data = [
        [
            Paragraph("Subtotal:", subtotal_label_style),
            Paragraph(amount_str, subtotal_value_style),
        ],
        [
            Paragraph("Tax:", subtotal_label_style),
            Paragraph("NPR 0", subtotal_value_style),
        ],
        [
            Paragraph("<b>TOTAL</b>", total_label_style),
            Paragraph(amount_str, total_value_style),
        ],
    ]

    totals_table = Table(
        totals_data,
        colWidths=[124 * mm, 50 * mm],
        style=TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            # Total row separator
            ('LINEABOVE', (0, 2), (-1, 2), 1.5, BRAND_PURPLE),
            ('TOPPADDING', (0, 2), (-1, 2), 12),
            ('BOTTOMPADDING', (0, 2), (-1, 2), 12),
            ('BACKGROUND', (0, 2), (-1, 2), BG_LIGHT),
        ]),
    )
    story.append(totals_table)
    story.append(Spacer(1, 20))

    # ═══════════════════════════════════════════════════════
    # TRANSACTION DETAILS
    # ═══════════════════════════════════════════════════════
    txn_id = bill_data.get('transaction_id')
    status = (bill_data.get('status') or 'paid').upper()

    status_colors = {
        'PAID': '#10b981',
        'PENDING': '#f59e0b',
        'REFUNDED': '#ef4444',
        'FAILED': '#ef4444',
    }
    status_color = status_colors.get(status, '#666666')

    status_style = ParagraphStyle(
        'Status',
        parent=styles['Normal'],
        fontSize=12,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor(status_color),
        alignment=TA_LEFT,
        leading=16,
    )

    txn_left = [
        Paragraph("PAYMENT STATUS", label_style),
        Spacer(1, 4),
        Paragraph(f"✓ {status}", status_style),
    ]

    txn_right = [
        Paragraph("TRANSACTION ID", label_style),
        Spacer(1, 4),
        Paragraph(_safe(txn_id), value_style),
        Spacer(1, 6),
        Paragraph(f"Issued: {_format_datetime(bill_data.get('issued_at'))}", ParagraphStyle(
            'IssuedAt',
            parent=styles['Normal'],
            fontSize=9,
            textColor=TEXT_LIGHT,
            leading=11,
        )),
    ]

    txn_table = Table(
        [[txn_left, txn_right]],
        colWidths=[87 * mm, 87 * mm],
        style=TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]),
    )
    story.append(txn_table)
    story.append(Spacer(1, 24))

    # ═══════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════
    story.append(Table(
        [['']],
        colWidths=[174 * mm],
        rowHeights=[0.5],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BORDER_GRAY),
        ]),
    ))
    story.append(Spacer(1, 10))

    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        textColor=TEXT_MUTED,
        alignment=TA_CENTER,
        leading=13,
    )

    story.append(Paragraph(
        "<b>Thank you for choosing SELTAI PREP!</b>",
        footer_style,
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "This is a computer-generated invoice and does not require a signature.<br/>"
        "For support or questions, contact us at "
        "<font color='#6c63ff'>seltaiprep@gmail.com</font>",
        footer_style,
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"© {datetime.now().year} SELTAI PREP · All rights reserved.",
        ParagraphStyle(
            'Copyright',
            parent=footer_style,
            fontSize=8,
            textColor=TEXT_LIGHT,
        ),
    ))

    # ═══════════════════════════════════════════════════════
    # BUILD
    # ═══════════════════════════════════════════════════════
    try:
        doc.build(story)
        logger.info(f" Bill PDF generated: {filepath}")
        return str(filepath)
    except Exception as e:
        logger.exception(f"Failed to build PDF for {bill_number}: {e}")
        raise