import os, sys
sys.path.insert(0, os.getcwd())
from shared.enterprise_documents import create_gst_invoice, create_ewaybill_draft, next_invoice_number
from shared.vendor_registration import parse_gst_certificate, parse_drug_license

# Test multi-item invoice
result = create_gst_invoice({
    'buyer_name': 'Test Buyer Pvt Ltd',
    'buyer_gstin': '27BBBBB0000B1Z5',
    'buyer_address': 'Mumbai',
    'buyer_place': 'Mumbai',
    'buyer_pin': '400001',
    'items': [
        {'name': 'Product A', 'hsn': '2933', 'qty': 2, 'unit': 'kg', 'rate': 1000, 'gst_rate': 18},
        {'name': 'Product B', 'hsn': '3004', 'qty': 5, 'unit': 'nos', 'rate': 200, 'gst_rate': 12},
    ],
    'vehicle_no': 'MP13AB1234',
    'transporter': 'Fast Transport',
    'distance': 800,
})
payload = result['payload']
print('Invoice no:', payload['DocDtls']['No'])
print('Items:', len(payload['ItemList']))
print('Totals:', payload['ValDtls'])
print('Vehicle:', payload['EwbDtls']['VehNo'])

# Test e-way from invoice data
eway = create_ewaybill_draft({'invoice_no': payload['DocDtls']['No'], 'to_pin': '400001'})
print('E-way vehicle:', eway['payload']['PartB']['vehicleNo'])
print('E-way transporter:', eway['payload']['PartB']['transporterName'])

# Test extraction
gst_text = """Legal Name of Business: ABC Pharma Pvt Ltd
Trade Name: ABC Pharma
Address: 123 Sector 5, Mumbai, Maharashtra - 400001
GSTIN: 27AABCU9603R1ZM
Drug License No: MH-123456"""
print('GST parse:', parse_gst_certificate(gst_text))
print('DL parse:', parse_drug_license(gst_text))
print('Next invoice no:', next_invoice_number())
