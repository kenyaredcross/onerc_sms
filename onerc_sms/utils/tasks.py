import frappe
from frappe.utils import now_datetime


def process_scheduled_campaigns():
    campaigns = frappe.get_all(
        "SMS Campaign",
        filters={
            "status": "Scheduled",
            "scheduled_at": ["<=", now_datetime()]
        },
        fields=["name"]
    )

    for campaign in campaigns:
        try:
            doc = frappe.get_doc("SMS Campaign", campaign.name)
            doc.send_campaign()
            frappe.db.commit()

        except Exception as e:
            frappe.log_error(
                f"Failled to process scheduled campaign {campaign.name}: {str(e)}",
                "SMS Campaign Scheduler"
            )
            frappe.db.commit()