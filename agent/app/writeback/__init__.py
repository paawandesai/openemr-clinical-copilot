"""
Writing extracted lab values back into OpenEMR — the agent's half of D1.

`plan.py` turns an extraction into a `LabWriteBackRequest` and says which
rows it refused to include. `client.py` posts it under the clinician's own
token. Nothing here holds a credential of its own.
"""
