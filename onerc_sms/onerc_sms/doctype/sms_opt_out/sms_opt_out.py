import frappe
from frappe.model.document import Document


class SMSOptOut(Document):

    def before_save(self):
        self.validate_phone_number()
        self.check_duplicate()

    def validate_phone_number(self):
        if not self.phone_number.startswith("+"):
            frappe.throw(f"Phone number {self.phone_number} must include a country code e.g. +254")

    def check_duplicate(self):
        existing = frappe.db.exists("SMS Opt-Out", {"phone_number": self.phone_number, "name": ["!=", self.name]})

        if existing:
            frappe.throw(f"{self.phone_number} is already on the opt-out list.")