# Copyright (c) 2026, Kelvin Njenga and Contributors
# See license.txt

"""What a campaign's filter rows mean, and what it refuses to save.

The filter grid is the only part of this app where somebody can express
something subtly wrong and get no error -- a campaign resolves perfectly well
against the wrong audience -- so the translation from grid rows to a query
filter is worth pinning down row by row.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date

from onerc_sms.onerc_sms.doctype.sms_campaign import filters
from onerc_sms.onerc_sms.doctype.sms_campaign.sms_campaign import SMSCampaign

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def row(field, operator, value=None, idx=1):
	"""One filter grid row, without needing a campaign to hang it off."""
	return frappe._dict(idx=idx, filter_field=field, operator=operator, filter_value=value)


class IntegrationTestSMSCampaign(IntegrationTestCase):
	"""Integration tests for SMSCampaign."""

	pass


class TestCampaignScheduling(IntegrationTestCase):
	def test_a_datetime_string_from_the_desk_can_be_compared_on_submit(self):
		campaign = SMSCampaign(
			{
				"doctype": "SMS Campaign",
				"scheduled_at": str(add_to_date(None, days=1)),
			}
		)

		with patch.object(campaign, "db_set") as db_set:
			campaign.on_submit()

		db_set.assert_called_once_with("status", "Scheduled")


class TestFilterRowsBecomeAQueryFilter(IntegrationTestCase):
	def test_each_operator_translates_to_the_one_frappe_uses(self):
		expected = {
			("Equals", "Active"): ["status", "=", "Active"],
			("Not Equals", "Active"): ["status", "!=", "Active"],
			("In", "Active, Retired"): ["status", "in", ["Active", "Retired"]],
			("Not In", "Retired"): ["status", "not in", ["Retired"]],
			(">", "2026-01-01"): ["status", ">", "2026-01-01"],
			("<=", "9"): ["status", "<=", "9"],
		}

		for (operator, value), want in expected.items():
			with self.subTest(operator=operator):
				self.assertEqual(filters.build([row("status", operator, value)]), [want])

	def test_a_wildcard_is_added_only_when_the_person_did_not_write_one(self):
		# Somebody typing "Arusha" under Like means "contains Arusha". Somebody
		# typing "Arusha%" has said where the wildcard goes and is left alone.
		self.assertEqual(filters.build([row("name", "Like", "Arusha")]), [["name", "like", "%Arusha%"]])
		self.assertEqual(filters.build([row("name", "Like", "Arusha%")]), [["name", "like", "Arusha%"]])

	def test_presence_operators_need_no_value(self):
		self.assertEqual(filters.build([row("phone", "Is Set")]), [["phone", "is", "set"]])
		self.assertEqual(filters.build([row("phone", "Is Not Set")]), [["phone", "is", "not set"]])

	def test_between_becomes_a_pair(self):
		self.assertEqual(
			filters.build([row("creation", "Between", "2026-01-01, 2026-06-30")]),
			[["creation", "between", ["2026-01-01", "2026-06-30"]]],
		)

	def test_two_rows_on_one_field_both_survive(self):
		"""The bug this module exists to fix.

		Built as a dict keyed by fieldname, "enrolled after January" and
		"enrolled before June" collapsed into whichever came last, and the
		campaign silently addressed five months more people than intended.
		"""
		built = filters.build(
			[
				row("creation", ">", "2026-01-01", idx=1),
				row("creation", "<", "2026-06-30", idx=2),
			]
		)

		self.assertEqual(len(built), 2)
		self.assertEqual(built[0][1], ">")
		self.assertEqual(built[1][1], "<")

	def test_the_words_this_table_used_before_still_resolve(self):
		# A filter row saved under the old grid must keep meaning what it meant.
		self.assertEqual(filters.build([row("status", "contains", "Act")]), [["status", "like", "%Act%"]])
		self.assertEqual(filters.build([row("status", "greater than", "A")]), [["status", ">", "A"]])
		self.assertEqual(filters.build([row("status", "is set")]), [["status", "is", "set"]])

	def test_what_it_builds_is_something_the_query_engine_accepts(self):
		"""Translation is only correct if the result actually runs."""
		built = filters.build(
			[row("name", "Is Set", idx=1), row("name", "Like", "SMS", idx=2)]
		)

		# No assertion on the count: the point is that the query engine takes
		# the structure without raising.
		frappe.get_all("SMS Campaign", filters=built, fields=["name"])


class TestFilterRowsAreCheckedOnSave(IntegrationTestCase):
	SOURCE = "SMS Campaign"

	def test_a_field_that_is_not_on_the_doctype_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			filters.validate(self.SOURCE, [row("not_a_real_field", "Equals", "x")])

	def test_an_operator_this_app_cannot_use_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			filters.validate(self.SOURCE, [row("status", "sort of like", "x")])

	def test_an_operator_that_needs_a_value_is_refused_without_one(self):
		with self.assertRaises(frappe.ValidationError):
			filters.validate(self.SOURCE, [row("status", "Equals", "   ")])

	def test_between_insists_on_exactly_two_values(self):
		with self.assertRaises(frappe.ValidationError):
			filters.validate(self.SOURCE, [row("creation", "Between", "2026-01-01")])

	def test_a_standard_column_is_a_field_like_any_other(self):
		# "everybody enrolled since the flood" is one of the more useful filters
		# a coordinator can write, and `creation` is not in Meta.fields.
		filters.validate(self.SOURCE, [row("creation", ">", "2026-01-01")])

	def test_rows_that_are_right_raise_nothing(self):
		filters.validate(
			self.SOURCE,
			[
				row("status", "Equals", "Draft", idx=1),
				row("campaign_name", "Like", "flood", idx=2),
				row("scheduled_at", "Is Set", idx=3),
			],
		)


class TestACampaignResolvesItsAudience(IntegrationTestCase):
	"""The Doctype Query source, end to end, without a gateway in sight.

	Built against `User` rather than one of the society's own doctypes, because
	this app is installed alongside several products and none of them is
	something its own tests may assume.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		cls.tag = frappe.generate_hash(length=8)
		# `User.mobile_no` is unique, so the shared-household-phone case is
		# exercised against the pipeline directly further down rather than
		# being modelled here.
		cls.numbers = {
			"first": "+255700000001",
			"second": "+255700000002",
			# No country code, so the resolver drops it.
			"third": "0755000000",
		}

		for who, number in cls.numbers.items():
			user = frappe.new_doc("User")
			user.email = f"{who}.{cls.tag}@example.com"
			user.first_name = who.title()
			user.mobile_no = number
			user.enabled = 1
			user.flags.no_welcome_mail = True
			user.insert(ignore_permissions=True)
			cls.addClassCleanup(
				frappe.delete_doc, "User", user.name, force=True, ignore_permissions=True
			)

	def campaign(self, **overrides):
		doc = frappe.new_doc("SMS Campaign")
		doc.campaign_name = f"Test {self.tag}"
		doc.message = "Hello {{ first_name }}."
		doc.source_type = "Doctype Query"
		doc.source_doctype = "User"
		doc.phone_field = "mobile_no"
		doc.scheduled_at = frappe.utils.add_to_date(None, days=1)
		doc.update(overrides)
		doc.append("campaign_filters", {"filter_field": "email", "operator": "Like", "filter_value": self.tag})
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "SMS Campaign", doc.name, force=True, ignore_permissions=True
		)

		return doc

	def test_the_filter_narrows_to_the_people_it_names(self):
		doc = self.campaign()
		resolved = doc.resolve_contacts()

		self.assertEqual(len(resolved), 2, "the number without a country code should be dropped")

	def test_the_message_is_rendered_against_each_person_s_own_record(self):
		doc = self.campaign()
		recipients = doc.run_pipeline(doc.resolve_contacts())

		self.assertEqual(
			sorted(r["phone"] for r in recipients), ["+255700000001", "+255700000002"]
		)
		# Rendered per recipient, which is the whole reason the resolver carries
		# a context alongside the number.
		self.assertEqual(
			sorted(r["message"] for r in recipients), ["Hello First.", "Hello Second."]
		)

	def test_a_shared_number_is_texted_once(self):
		# A household phone against two people's records. Asserted on the
		# pipeline directly because `User.mobile_no` is unique and cannot hold
		# the case.
		doc = self.campaign()
		recipients = doc.run_pipeline(
			[
				{"phone": "+255700000009", "context": {"first_name": "Asha"}},
				{"phone": "+255700000009", "context": {"first_name": "Juma"}},
			]
		)

		self.assertEqual(len(recipients), 1)
		self.assertEqual(recipients[0]["message"], "Hello Asha.", "the first occurrence wins")

	def test_an_opted_out_number_is_never_reached(self):
		opt_out = frappe.get_doc(
			{"doctype": "SMS Opt Out", "phone_number": "+255700000001", "source": "Manual"}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "SMS Opt Out", opt_out.name, force=True, ignore_permissions=True
		)

		doc = self.campaign()
		numbers = [r["phone"] for r in doc.run_pipeline(doc.resolve_contacts())]

		self.assertNotIn("+255700000001", numbers)

	def test_a_filter_naming_a_field_that_is_not_there_is_refused_on_save(self):
		doc = frappe.new_doc("SMS Campaign")
		doc.campaign_name = f"Broken {self.tag}"
		doc.message = "Hello."
		doc.source_type = "Doctype Query"
		doc.source_doctype = "User"
		doc.phone_field = "mobile_no"
		doc.scheduled_at = frappe.utils.add_to_date(None, days=1)
		doc.append("campaign_filters", {"filter_field": "branch_name", "operator": "Equals", "filter_value": "x"})

		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_manual_campaign_is_not_asked_about_filters(self):
		# The filter grid belongs to the Doctype Query source. A Manual campaign
		# — which is what vmmsx's Communication console files — must not be
		# validated against a source doctype it does not have.
		doc = frappe.new_doc("SMS Campaign")
		doc.campaign_name = f"Manual {self.tag}"
		doc.message = "Hello."
		doc.source_type = "Manual"
		doc.phone_numbers = "+255700000001"
		doc.scheduled_at = frappe.utils.add_to_date(None, days=1)
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "SMS Campaign", doc.name, force=True, ignore_permissions=True
		)

		self.assertEqual(len(doc.resolve_contacts()), 1)
