import frappe
from frappe.model.document import Document


class SMSTemplate(Document):

    def validate(self):
        self.validate_message_length()
        self.extract_variables()

    def validate_message_length(self):
        if len(self.message) > 160:
            frappe.msgprint(
                f"Message is {len(self.message)} characters. Messages over 160 characters will be sent as multiple SMS and cost more.",
                indicator="orange",
                alert=True
            )

    def extract_variables(self):
        import re
        variables = re.findall(r"\{\{\s*(\w+)\s*\}\}", self.message)
        if variables:
            unique_vars = list(dict.fromkeys(variables))
            frappe.msgprint(
                f"Variables found in template: {', '.join(unique_vars)}",
                indicator="blue",
                alert=True
            )