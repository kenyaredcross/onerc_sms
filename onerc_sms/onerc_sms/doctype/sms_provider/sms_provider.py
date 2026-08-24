# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""One gateway's credentials, and the guarantee that only one of them sends."""

import frappe
from frappe import _
from frappe.model.document import Document

from onerc_sms.utils.providers import ADAPTERS


class SMSProvider(Document):
	def validate(self):
		self.validate_adapter()
		self.validate_credentials()

	def validate_adapter(self):
		"""Refuse an adapter nothing can send through.

		`Custom` is still on the Select for anybody extending this app, but it
		is caught here rather than at send time -- a campaign that reached the
		provider and then found no adapter would already be marked Sending with
		a delivery log full of one identical error.
		"""
		if self.adapter and self.adapter not in ADAPTERS:
			frappe.throw(
				_("This app can send through {0}. <b>{1}</b> has no adapter implemented.").format(
					" and ".join(f"<b>{name}</b>" for name in ADAPTERS), self.adapter
				),
				title=_("Adapter Not Supported"),
			)

	def validate_credentials(self):
		"""Ask for the sender the chosen gateway insists on.

		Twilio has nowhere to send from without one. Africa's Talking will
		deliver on a shared sender when it is blank, which is exactly what a
		sandbox account needs, so it stays optional there -- the difference
		lives in `ADAPTERS`, not in a branch here.
		"""
		spec = ADAPTERS.get(self.adapter)

		if not spec:
			return

		if spec["sender_required"] and not (self.sender_id or "").strip():
			frappe.throw(
				_("{0} needs a <b>{1}</b>.").format(self.adapter, spec["sender_id_label"]),
				title=_("Sender Missing"),
			)

	def before_save(self):
		# Only one SMS Provider can be active at a time. Deactivate the rest once it is set

		if self.is_active:
			frappe.db.set_value(
				"SMS Provider",
				{"name": ["!=", self.name], "is_active": 1},
				"is_active",
				0,
			)
