import frappe

@frappe.whitelist()

def get_doctype_fields(doctype):
    meta = frappe.get_meta(doctype)

    fields = [
        field.fieldname 
        for field in meta.fields
        if field.fieldtype in ["Data", "Phone"]
    ]

    return fields

@frappe.whitelist()
def send_now(campaign):
    doc = frappe.get_doc("SMS Campaign", campaign)

    if doc.docstatus != 1:
        frappe.throw("Campaign must be submitted before sending.")

    doc.send_campaign()
    frappe.db.commit()

    return "OK"