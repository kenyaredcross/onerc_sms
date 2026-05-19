import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime
from onerc_sms.utils.providers import get_active_provider, send_via_provider


class SMSCampaign(Document):

    def validate(self):
        self.validate_message()
        self.validate_source()

    def on_submit(self):
        if self.scheduled_at <= now_datetime():
            self.send_campaign()
        else:
            self.db_set("status", "Scheduled")

    def validate_message(self):
        if not self.template and not self.message:
            frappe.throw("Please provide a message or select a template.")

        if self.template and not self.message:
            template = frappe.get_doc("SMS Template", self.template)
            self.message = template.message

    def validate_source(self):
        if self.source_type == "Doctype Query" and not self.source_doctype:
            frappe.throw("Please select a Source Doctype.")

        if self.source_type == "Doctype Query" and not self.phone_field:
            frappe.throw("Please select a Phone Field.")

        if self.source_type == "CSV Upload" and not self.csv_file:
            frappe.throw("Please attach a CSV file.")

        if self.source_type == "Manual" and not self.phone_numbers:
            frappe.throw("Please enter phone numbers.")

    def send_campaign(self):
        self.db_set("status", "Sending")

        recipients = self.resolve_contacts()
        recipients = self.run_pipeline(recipients)

        if not recipients:
            self.db_set("status", "Failed")
            frappe.throw("No valid recipients found after filtering.")

        self.send_sms(recipients)

    def resolve_contacts(self):
        if self.source_type == "Doctype Query":
            return self.resolve_from_doctype()

        elif self.source_type == "CSV Upload":
            return self.resolve_from_csv()

        elif self.source_type == "Manual":
            return self.resolve_from_manual()

    def resolve_from_doctype(self):
        if not self.source_doctype or not self.phone_field:
            return []

        filters = self.build_filters()

        records = frappe.get_all(
            self.source_doctype,
            filters=filters,
            fields=["*"]
        )

        recipients = []

        for record in records:
            phone = record.get(self.phone_field, "")

            if not phone:
                continue

            phone = str(phone).strip()

            if not phone.startswith("+"):
                frappe.log_error(
                    f"Skipping {phone} in {self.source_doctype} — missing country code",
                    "SMS Campaign"
                )
                continue

            recipients.append({
                "phone": phone,
                "context": record
            })

        return recipients

    def build_filters(self):
        filters = {}

        operator_map = {
            "equals": "=",
            "not equals": "!=",
            "contains": "like",
            "does not contain": "not like",
            "greater than": ">",
            "less than": "<",
            "is set": "is",
            "is not set": "is"
        }

        for row in self.campaign_filters:
            operator = operator_map.get(row.operator, "=")

            if row.operator == "contains":
                value = f"%{row.filter_value}%"
            elif row.operator == "does not contain":
                value = f"%{row.filter_value}%"
            elif row.operator == "is set":
                value = "set"
            elif row.operator == "is not set":
                value = "not set"
            else:
                value = row.filter_value

            filters[row.filter_field] = [operator, value]

        return filters

    def resolve_from_csv(self):
        import csv
        import io

        if not self.csv_file:
            return []

        file_doc = frappe.get_doc("File", {"file_url": self.csv_file})
        file_content = file_doc.get_content()

        if isinstance(file_content, bytes):
            file_content = file_content.decode("utf-8")

        reader = csv.DictReader(io.StringIO(file_content))

        if "phone_number" not in reader.fieldnames:
            frappe.throw("CSV must have a column named phone_number")

        recipients = []

        for row in reader:
            phone = row.get("phone_number", "").strip()

            if not phone:
                continue

            if not phone.startswith("+"):
                frappe.log_error(
                    f"Skipping {phone} — missing country code",
                    "SMS Campaign"
                )
                continue

            context = {k: v for k, v in row.items() if k != "phone_number"}

            recipients.append({
                "phone": phone,
                "context": context
            })

        return recipients

    def resolve_from_manual(self):
        if not self.phone_numbers:
            return []

        numbers = [
            n.strip()
            for n in self.phone_numbers.splitlines()
            if n.strip()
        ]

        recipients = []

        for number in numbers:
            if not number.startswith("+"):
                frappe.log_error(
                    f"Skipping {number} — missing country code",
                    "SMS Campaign"
                )
                continue

            recipients.append({
                "phone": number,
                "context": {}
            })

        return recipients

    def run_pipeline(self, recipients):
        recipients = self.filter_opted_out(recipients)
        recipients = self.deduplicate(recipients)
        recipients = self.render_messages(recipients)
        return recipients

    def filter_opted_out(self, recipients):
        opted_out = frappe.db.get_all(
            "SMS Opt-Out",
            fields=["phone_number"]
        )

        opted_out_numbers = {row.phone_number for row in opted_out}

        return [
            r for r in recipients
            if r["phone"] not in opted_out_numbers
        ]

    def deduplicate(self, recipients):
        seen = set()
        unique = []

        for r in recipients:
            if r["phone"] not in seen:
                seen.add(r["phone"])
                unique.append(r)

        return unique

    def render_messages(self, recipients):
        rendered = []

        for r in recipients:
            try:
                message = frappe.render_template(self.message, r["context"])
                rendered.append({
                    "phone": r["phone"],
                    "context": r["context"],
                    "message": message
                })
            except Exception as e:
                frappe.log_error(
                    f"Failed to render message for {r['phone']}: {str(e)}",
                    "SMS Campaign"
                )
                continue

        return rendered

    def send_sms(self, recipients):
        provider = get_active_provider()

        total_sent = 0
        total_failed = 0
        total_cost = 0.0

        self.db_set("total_recipients", len(recipients))

        for recipient in recipients:
            result = send_via_provider(
                provider,
                recipient["phone"],
                recipient["message"]
            )

            if result["status"] == "Success":
                total_sent += 1
            else:
                total_failed += 1

            total_cost += result["cost"]

            self.append("delivery_log", {
                "phone_number": recipient["phone"],
                "status": result["status"],
                "status_code": result["status_code"],
                "cost": result["cost"],
                "message_id": result["message_id"],
                "error": result["error"],
                "timestamp": now_datetime()
            })

        self.db_set("total_sent", total_sent)
        self.db_set("total_failed", total_failed)
        self.db_set("total_cost", total_cost)
        self.db_set("status", "Sent" if total_failed == 0 else "Failed")

        self.flags.ignore_validate_update_after_submit = True
        self.save(ignore_permissions=True)