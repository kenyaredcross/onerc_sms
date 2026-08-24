### OneRC SMS

SMS module for OneRC Core — campaign building, an approval workflow, and
delivery logging, on top of Africa's Talking or Twilio.

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/<org>/onerc_sms --branch vmmsx-edition
bench --site <site> install-app onerc_sms
```

Both gateway SDKs (`africastalking`, `twilio`) are declared as dependencies, so
`bench get-app` installs them. There is nothing to `pip install` by hand.

On an existing site, `bench --site <site> migrate` is enough — it runs the
approval-workflow installer and the patch that moves any API key already on the
site into encrypted storage.

### Setting up a gateway

Everything below is done once, on the desk, by a System Manager. Go to
**OneRC SMS → SMS Provider → New**.

1. **Provider Name** — your own label, e.g. `Africa's Talking (Live)`.
2. **Adapter** — pick this first. The three credential fields opposite are
   relabelled to the words your gateway uses.
3. Paste the credentials:

   | Adapter | Username / Account SID | API Key / Auth Token | Sender ID / From Number |
   | --- | --- | --- | --- |
   | **Africa's Talking** | your AT username | Dashboard → Settings → API Key | your registered sender ID or short code, e.g. `REDCROSS` — may be left blank |
   | **Twilio** | Console → Account SID (starts `AC`) | Console → Auth Token | a Twilio number in E.164 form (`+15017122661`) **or** a Messaging Service SID (starts `MG`) — this field accepts both |

4. Tick **Is Active**. Only one provider is ever active; ticking this unticks
   the rest.
5. Save. The **Status** section at the bottom says whether the record can send,
   and names the field to fill in if it cannot.
6. Press **Send Test SMS** and give it your own number in international form.
   This sends a real message, which is the only way to find out about a rejected
   sender ID, an empty balance, or an unverified number on a Twilio trial.

**Testing without spending credit.** Set the Africa's Talking username to
`sandbox` and use your sandbox API key — the SDK routes to their sandbox host
automatically. Leave the sender ID blank there.

The API key is stored encrypted (`Password` field), so it is not readable from a
report or a database dump.

### Building a campaign

**OneRC SMS → SMS Campaign → New**. Three ways to name an audience:

- **Doctype Query** — pick a source doctype and a phone field, then add filter
  rows. The **Field** column offers that doctype's fields and nothing else, by
  their form labels, and the operators are the ones the desk's own list filters
  use (`Equals`, `Like`, `In`, `Between`, `Is Set`, …). Filters on the same
  field combine rather than replacing each other, so "enrolled after January"
  and "before June" means both.

  If the records carry no phone field of their own, name one on a linked
  doctype instead — `red_profile.phone` reads the number off the linked Red
  Profile.

  The query runs with the **campaign owner's** permissions, so geo scoping
  applies: a branch coordinator's campaign reaches their branch, not the country.

- **CSV Upload** — a file with a `phone_number` column. Every other column
  becomes template context for that row.

- **Manual** — one number per line.

Every number must carry a country code (`+255…`); numbers without one are
dropped and logged.

Use **Preview** to see the recipient count and the first few rendered messages
before anything is sent.

### Approval

Campaigns go through the **SMS Campaign Approval** workflow:
`Draft → Pending → Approved`, with `Rejected → Pending` for a resubmission.
A campaign cannot be submitted until it reaches **Approved**.

Two roles carry it: **SMS Campaign Manager** (writes and submits) and
**SMS Campaign Approver** (approves or rejects). Assign them under
**Users → Role**. The workflow is installed automatically on migrate.

### Sending

Submitting a campaign whose **Scheduled At** has passed sends it immediately.
Anything later is queued and picked up by a scheduler that runs every five
minutes; **Send Now** on the form overrides it.

Every recipient gets a row in the campaign's delivery log — status, the
gateway's own status code, cost, message ID and any error — and the campaign's
totals are written back when the run finishes.

> Sending is synchronous and one message per API call, so a campaign to several
> thousand people will hold a worker for a long time. Batch dispatch is not
> implemented yet.

### Opting out

Numbers on **SMS Opt Out** are filtered from every campaign. There is no inbound
webhook, so STOP replies are not captured automatically — opt-outs are entered
by hand or imported.

### Tests

```bash
bench --site <site> run-tests --app onerc_sms
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/onerc_sms
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### License

mit
