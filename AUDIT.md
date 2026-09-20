# AUDIT.md

The full audit lives at [docs/AUDIT.md](docs/AUDIT.md) — the Stage 3 audit of
this OpenEMR fork (security, performance, architecture, data quality, and
compliance & regulatory), opening with a one-page executive summary of the
most impactful findings. The raw evidence ledger behind it is
[docs/audit/findings.md](docs/audit/findings.md).

The three findings that most decide whether this codebase is safe to build a
co-pilot on, from that summary:

1. **Docker's port-publishing bypasses the host firewall entirely, and this
   was live (F-54).** MariaDB (default `root`/`root`) and phpMyAdmin sat
   reachable from the public internet for a bounded window, with UFW's
   default-deny policy never seeing the traffic.
2. **Lab results carry no reference ranges or abnormal flags, ever (F-19).**
   Every one of 7,259–29,701 result rows has a value and a LOINC code but a
   permanently blank `range`/`abnormal` field, so a co-pilot cannot honestly
   call a value high, low or normal from this record alone.
3. **Authorization has no concept of "this patient" (F-50).** Both the ACL
   and OAuth-scope layers gate by section and action only, so any clinician
   token with general chart access can read any patient's FHIR record.
