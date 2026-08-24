# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""Move any API key still sitting in plain text into encrypted storage.

`SMS Provider.api_key` became a Password field, which means Frappe keeps the
value encrypted in `__Auth` and leaves a mask in the table's own column. A key
saved before that change is still sitting in the column as plain text, readable
by anybody with a report on the doctype or a look at a database backup.

Nothing breaks without this patch -- `get_password` returns an unmasked
in-memory value as-is, which is exactly what makes the changeover seamless --
so this is about the key at rest, not about sending working.

**Runs in `pre_model_sync`**, before the schema change it exists because of.
Ordered after the sync, the column has already been rewritten by the time this
looks at it and there is nothing left to migrate -- the key is simply gone and
an operator has to paste it in again. Before the sync, `api_key` is still an
ordinary column holding the plain value, which is exactly what this needs.

Idempotent: a value that is already a mask is left alone, and the patch may be
re-run safely. A site installing the app for the first time has no SMS Provider
table yet, which the guard below covers.
"""

import frappe
from frappe.utils.password import set_encrypted_password

DOCTYPE = "SMS Provider"
FIELD = "api_key"

#: What Frappe writes into the column in place of a Password value. Recognised
#: the way `BaseDocument.is_dummy_password` does -- every character is an
#: asterisk -- rather than by matching a fixed width.
MASK = "*" * 10


def execute():
	# Both guards matter in `pre_model_sync`: on a first install neither the
	# doctype nor its table exists yet, and on an older site the column may
	# predate the field.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	if not frappe.db.table_exists(DOCTYPE) or not frappe.db.has_column(DOCTYPE, FIELD):
		return

	for name, key in frappe.db.get_all(DOCTYPE, fields=["name", FIELD], as_list=True):
		if not key or _is_masked(key):
			continue

		# Written straight through the password utility and the column rather
		# than through `save()`: saving would run the controller's before_save,
		# which deactivates other providers, and a patch has no business
		# changing which gateway a site sends through.
		set_encrypted_password(DOCTYPE, name, key, FIELD)
		frappe.db.set_value(DOCTYPE, name, FIELD, MASK, update_modified=False)


def _is_masked(value: str) -> bool:
	return bool(value) and set(value) == {"*"}
