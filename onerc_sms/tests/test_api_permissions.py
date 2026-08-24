# Copyright (c) 2026, Kelvin Njenga and Contributors
# See license.txt

"""Who may call the campaign endpoints.

`frappe.get_doc` performs no permission check unless it is asked to, so a
whitelisted method that only calls it is reachable by any signed-in session --
including a volunteer on the portal, who would get back the recipients' phone
numbers and the exact words about to be sent to them.

Both halves matter and both are asserted here: the endpoints refuse somebody
with no business calling them, **and** they do not refuse the people whose job
this is. A permission check that also blocks the campaign manager is not a fix.
"""

import frappe
from frappe.tests import IntegrationTestCase

from onerc_sms.api import campaign as campaign_api

MANAGER_ROLE = "SMS Campaign Manager"


class CampaignFixture(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def make_user(self, roles=()):
		user = frappe.new_doc("User")
		user.email = f"{frappe.generate_hash(length=10)}@example.com"
		user.first_name = "Test"
		user.enabled = 1
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)

		for role in roles:
			user.add_roles(role)

		self.addCleanup(frappe.delete_doc, "User", user.name, force=True, ignore_permissions=True)

		return user.name

	def make_campaign(self):
		doc = frappe.new_doc("SMS Campaign")
		doc.campaign_name = f"Perm {frappe.generate_hash(length=6)}"
		doc.message = "Hello."
		doc.source_type = "Manual"
		doc.phone_numbers = "+255700000001"
		doc.scheduled_at = frappe.utils.add_to_date(None, days=1)
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "SMS Campaign", doc.name, force=True, ignore_permissions=True
		)

		return doc


class TestTheEndpointsRefuseAStranger(CampaignFixture):
	def test_preview_does_not_hand_phone_numbers_to_just_anybody(self):
		doc = self.make_campaign()
		frappe.set_user(self.make_user())

		with self.assertRaises(frappe.PermissionError):
			campaign_api.preview_campaign(doc.name)

	def test_send_now_cannot_be_triggered_by_just_anybody(self):
		doc = self.make_campaign()
		frappe.set_user(self.make_user())

		with self.assertRaises(frappe.PermissionError):
			campaign_api.send_now(doc.name)

	def test_the_field_pickers_are_closed_too(self):
		# They enumerate the schema of any doctype named, which is not something
		# to hand out to a portal session.
		frappe.set_user(self.make_user())

		with self.assertRaises(frappe.PermissionError):
			campaign_api.get_filter_fields("User")

		with self.assertRaises(frappe.PermissionError):
			campaign_api.get_doctype_fields("User")


class TestTheEndpointsStillWorkForThePeopleWhoseJobThisIs(CampaignFixture):
	def test_a_campaign_manager_may_preview(self):
		doc = self.make_campaign()
		frappe.set_user(self.make_user(roles=[MANAGER_ROLE]))

		answer = campaign_api.preview_campaign(doc.name)

		self.assertEqual(answer["total"], 1)
		self.assertEqual(answer["preview"][0]["phone"], "+255700000001")

	def test_a_campaign_manager_may_read_the_field_pickers(self):
		frappe.set_user(self.make_user(roles=[MANAGER_ROLE]))

		fields = campaign_api.get_filter_fields("SMS Campaign")
		values = {field["value"] for field in fields}

		self.assertIn("status", values)
		self.assertIn("creation", values, "standard columns should be offered too")
		self.assertNotIn("section_break_1", values, "layout fields are not filterable")

	def test_the_picker_labels_a_field_the_way_the_form_does(self):
		fields = {field["value"]: field for field in campaign_api.get_filter_fields("SMS Campaign")}

		self.assertEqual(fields["campaign_name"]["label"], "Campaign Name")
		self.assertIn("campaign_name", fields["campaign_name"]["description"])

	def test_administrator_is_unaffected(self):
		doc = self.make_campaign()

		self.assertEqual(campaign_api.preview_campaign(doc.name)["total"], 1)


class TestSubmitIsTheRightPermissionForSendNow(CampaignFixture):
	def test_a_submitted_campaign_can_still_be_checked_for_submit(self):
		"""The check must not be one no live campaign can pass.

		`send_now` exists to dispatch a campaign that is already submitted, so
		it asks for `submit` on a docstatus 1 document. Frappe's permission
		layer does not gate ptypes on docstatus -- this is what notices if that
		ever changes.
		"""
		doc = self.make_campaign()
		doc.db_set("docstatus", 1)
		# Registered after make_campaign's delete, so it runs first: Frappe
		# refuses to delete a submitted document even with force.
		self.addCleanup(
			frappe.db.set_value, "SMS Campaign", doc.name, "docstatus", 0, update_modified=False
		)
		doc.reload()

		frappe.set_user(self.make_user(roles=[MANAGER_ROLE]))
		reloaded = frappe.get_doc("SMS Campaign", doc.name)

		self.assertTrue(reloaded.has_permission("submit"))
