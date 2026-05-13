# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SMSProvider(Document):
	def before_save(self):
		# Only one SMS Provider can be active at a time. Deactivate the rest once it is set

		if self.is_active:
			frappe.db.set_value(
				"SMS Provider",
				{"name": ["!=", self.name], "is_active": 1},
				"is_active",
				0,
			)
