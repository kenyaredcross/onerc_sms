# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""Turning the campaign's filter rows into a Frappe query filter.

The rows a coordinator builds in the grid are the same thing the desk's own list
view filters are, and they are translated into the same structure Frappe's query
engine already speaks -- a **list** of ``[fieldname, operator, value]`` triples::

    [["status", "=", "Active"], ["branch", "in", ["Arusha", "Dodoma"]]]

A list and not a dict, which is what this used to build. A dict is keyed by
fieldname, so a coordinator filtering *enrolled after January* and *before June*
silently kept only the second row and addressed five months more people than
they meant to. Nothing warned them, because the campaign resolved perfectly
well -- against the wrong audience.

**The vocabulary is Frappe's.** Somebody who has filtered a list in the desk
already knows what Like and In do, and the words on the grid are the words they
have already used. The lowercase set this table shipped with earlier is still
accepted, so a filter row saved before this change keeps meaning what it meant.

**Every row is validated when the campaign is saved, not when it sends.** A
field that does not exist on the source doctype is a typo somebody can fix while
they are looking at the form; the same typo discovered at send time is a
half-built campaign, a raw SQL error in a log, and a broadcast that did not go
out.
"""

import frappe
from frappe import _

#: Every operator the grid offers, and what the query engine calls it. The
#: lowercase keys are the vocabulary this doctype shipped with before the grid
#: spoke Frappe's; they are kept so an existing filter row still resolves.
OPERATORS = {
	# The vocabulary the desk's own list filters use, which is what the grid now
	# offers. Compared lowercased, so "Not Equals" on the form finds this row.
	"equals": "=",
	"not equals": "!=",
	"like": "like",
	"not like": "not like",
	"in": "in",
	"not in": "not in",
	">": ">",
	"<": "<",
	">=": ">=",
	"<=": "<=",
	"between": "between",
	"is set": "is",
	"is not set": "is",
	# Words this table shipped with before, kept so a filter row saved under the
	# old grid still means what it meant.
	"contains": "like",
	"does not contain": "not like",
	"greater than": ">",
	"less than": "<",
}

#: Operators that answer from the field alone. `filter_value` is ignored for
#: these, and the grid stops asking for one.
VALUELESS = {"is set", "is not set"}

#: Operators whose value is a list rather than a scalar, entered comma-separated.
LIST_VALUED = {"in", "not in", "between"}

#: What a wildcard operator does to a value that carries no wildcard of its own.
#: A coordinator typing `Arusha` under Like means "contains Arusha"; one typing
#: `Arusha%` has said exactly where the wildcard goes and is left alone.
WILDCARD = "%"


def build(rows) -> list[list]:
	"""The filter list for `frappe.get_list`, from the campaign's filter rows."""
	filters = []

	for row in rows or []:
		operator = _operator(row)

		if operator in VALUELESS:
			# Frappe's own spelling for a presence test: ["field", "is", "set"].
			filters.append([row.filter_field, "is", "set" if operator == "is set" else "not set"])
			continue

		filters.append([row.filter_field, OPERATORS[operator], _value(operator, row.filter_value)])

	return filters


def validate(source_doctype: str, rows) -> None:
	"""Refuse a filter row that cannot resolve, while the form is still open.

	Three things are worth catching here and nowhere else: an operator this app
	does not know, a field that is not on the source doctype, and a missing
	value for an operator that needs one.
	"""
	if not rows:
		return

	if not source_doctype:
		return

	meta = frappe.get_meta(source_doctype)

	for row in rows:
		operator = _operator(row, throw_on_unknown=True)

		if not meta.has_field(row.filter_field) and row.filter_field not in _standard_fields():
			frappe.throw(
				_("Row {0}: <b>{1}</b> is not a field on {2}.").format(
					row.idx, row.filter_field, frappe.bold(source_doctype)
				),
				title=_("Unknown Field"),
			)

		if operator in VALUELESS:
			continue

		if row.filter_value is None or str(row.filter_value).strip() == "":
			frappe.throw(
				_("Row {0}: <b>{1}</b> needs a value. Use <b>Is Set</b> or <b>Is Not Set</b> to"
				  " filter on whether the field is filled in at all.").format(row.idx, row.operator),
				title=_("Filter Value Needed"),
			)

		if operator == "between" and len(_split(row.filter_value)) != 2:
			frappe.throw(
				_("Row {0}: <b>Between</b> takes exactly two values, comma-separated.").format(row.idx),
				title=_("Two Values Needed"),
			)


def _operator(row, throw_on_unknown: bool = False) -> str:
	operator = (row.operator or "").strip().lower()

	if operator not in OPERATORS:
		if throw_on_unknown:
			frappe.throw(
				_("Row {0}: <b>{1}</b> is not an operator this app can use.").format(
					row.idx, row.operator
				),
				title=_("Unknown Operator"),
			)

		# Reached only for a row that was already saved when the vocabulary
		# changed under it. Equality is the least surprising reading, and
		# `validate` refuses to let a new one through.
		return "equals"

	return operator


def _value(operator: str, raw):
	"""The value in the shape the query engine wants for this operator."""
	value = "" if raw is None else str(raw).strip()

	if operator in LIST_VALUED:
		return _split(value)

	if operator in ("contains", "like", "does not contain", "not like") and WILDCARD not in value:
		return f"{WILDCARD}{value}{WILDCARD}"

	return value


def _split(value) -> list[str]:
	return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _standard_fields() -> set[str]:
	"""Columns every doctype has, which `Meta.has_field` does not report.

	Filtering on `creation` is one of the more useful things a coordinator can
	do -- "everybody enrolled since the flood" -- and it would otherwise be
	rejected as an unknown field.
	"""
	from frappe.model import default_fields, optional_fields

	return set(default_fields) | set(optional_fields)
