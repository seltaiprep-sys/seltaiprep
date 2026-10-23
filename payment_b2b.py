"""B2B Payment Helpers — eSewa + Khalti"""
import os
import hmac
import hashlib
import base64
import requests
from datetime import datetime


ESEWA_ENV = os.environ.get('ESEWA_ENVIRONMENT', 'test')
ESEWA_MERCHANT_CODE = os.environ.get('ESEWA_MERCHANT_CODE', 'EPAYTEST')
ESEWA_SECRET_KEY = os.environ.get('ESEWA_SECRET_KEY', '8gBm/:&EnhH.1/q')

if ESEWA_ENV == 'production':
    ESEWA_BASE = 'https://epay.esewa.com.np'
else:
    ESEWA_BASE = 'https://rc-epay.esewa.com.np'

ESEWA_INITIATE_URL = ESEWA_BASE + '/api/epay/main/v2/form'
ESEWA_STATUS_URL = ESEWA_BASE + '/api/epay/transaction/status/'


def esewa_generate_signature(total_amount, transaction_uuid, product_code):
    message = 'total_amount=' + str(total_amount) + ',transaction_uuid=' + transaction_uuid + ',product_code=' + product_code
    sig = hmac.new(
        ESEWA_SECRET_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()
    return base64.b64encode(sig).decode('utf-8')


def esewa_build_payment_data(purchase, partner_id, success_url, failure_url):
    amount = int(purchase.amount_npr)
    transaction_uuid = 'B2B-' + str(purchase.id) + '-' + str(partner_id) + '-' + str(int(datetime.now().timestamp()))
    product_code = ESEWA_MERCHANT_CODE
    signature = esewa_generate_signature(amount, transaction_uuid, product_code)
    return {
        'amount': amount,
        'tax_amount': 0,
        'total_amount': amount,
        'transaction_uuid': transaction_uuid,
        'product_code': product_code,
        'product_service_charge': 0,
        'product_delivery_charge': 0,
        'success_url': success_url,
        'failure_url': failure_url,
        'signed_field_names': 'total_amount,transaction_uuid,product_code',
        'signature': signature,
    }


def esewa_verify_payment(transaction_uuid, total_amount):
    params = {
        'product_code': ESEWA_MERCHANT_CODE,
        'total_amount': total_amount,
        'transaction_uuid': transaction_uuid,
    }
    try:
        r = requests.get(ESEWA_STATUS_URL, params=params, timeout=30)
        data = r.json()
        status = (data.get('status') or '').upper()
        return {'success': status == 'COMPLETE', 'status': status, 'data': data}
    except Exception as e:
        return {'success': False, 'status': 'ERROR', 'error': str(e)}


KHALTI_ENV = os.environ.get('KHALTI_ENVIRONMENT', 'test')
KHALTI_SECRET_KEY = os.environ.get('KHALTI_SECRET_KEY', '')
KHALTI_BASE = 'https://khalti.com/api/v2' if KHALTI_ENV == 'production' else 'https://a.khalti.com/api/v2'


def khalti_initiate_payment(purchase, partner, return_url, website_url):
    if not KHALTI_SECRET_KEY:
        return {'success': False, 'error': 'Khalti not configured'}
    payload = {
        'return_url': return_url,
        'website_url': website_url,
        'amount': int(purchase.amount_npr * 100),
        'purchase_order_id': 'B2B-' + str(purchase.id),
        'purchase_order_name': str(purchase.credits) + ' UKVI Credits',
        'customer_info': {
            'name': partner.name,
            'email': partner.email,
            'phone': partner.phone or '9800000000',
        },
    }
    headers = {'Authorization': 'Key ' + KHALTI_SECRET_KEY, 'Content-Type': 'application/json'}
    try:
        r = requests.post(KHALTI_BASE + '/epayment/initiate/', json=payload, headers=headers, timeout=30)
        data = r.json()
        if r.status_code == 200 and data.get('payment_url'):
            return {'success': True, 'payment_url': data['payment_url'], 'pidx': data['pidx']}
        return {'success': False, 'error': data.get('detail') or 'Khalti error', 'data': data}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def khalti_verify_payment(pidx):
    if not KHALTI_SECRET_KEY:
        return {'success': False, 'status': 'NOT_CONFIGURED'}
    headers = {'Authorization': 'Key ' + KHALTI_SECRET_KEY, 'Content-Type': 'application/json'}
    try:
        r = requests.post(KHALTI_BASE + '/epayment/lookup/', json={'pidx': pidx}, headers=headers, timeout=30)
        data = r.json()
        status = (data.get('status') or '').upper()
        return {'success': status == 'COMPLETED', 'status': status, 'data': data}
    except Exception as e:
        return {'success': False, 'status': 'ERROR', 'error': str(e)}
