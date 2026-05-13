import frappe
from frappe.model.document import Document


class OnercSMSSettings(Document):

    def validate(self):
        self.validate_quiet_hours()
        self.validate_admin_numbers()

    def validate_quiet_hours(self):
        if not self.quiet_hours_start or not self.quiet_hours_end:
            return

        if self.quiet_hours_start == self.quiet_hours_end:
            frappe.throw("Quiet hours start and end cannot be the same time.")

    def validate_admin_numbers(self):
        if not self.admin_phone_numbers:
            return

        numbers = [n.strip() for n in self.admin_phone_numbers.splitlines() if n.strip()]

        for number in numbers:
            if not number.startswith("+"):
                frappe.throw(f"Admin phone number {number} must start with a country code e.g. +254")

        self.admin_phone_numbers = "\n".join(numbers)