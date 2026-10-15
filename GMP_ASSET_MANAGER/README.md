# GMP Asset Manager

Equipment, instrument and reference-standard asset management for GMP-regulated
pharmaceutical and biotech facilities. It reads barcodes from any scanner and
enforces the controls that 21 CFR Part 11, EU GMP Annex 11 and ALCOA+ expect.

Built with Python 3.10+, FastAPI and SQLite. It has no other runtime dependencies.

## Quick start

```bash
cd GMP_ASSET_MANAGER
pip install -r requirements.txt
python run_server.py            # http://127.0.0.1:8000
```

On first start an `admin` account is created. The temporary password is printed
to the console, or you can set it with `GMP_ADMIN_PASSWORD`. The password must
be changed at first login. Then:

1. As **admin**, create users (qa, technician, operator, viewer) and locations.
2. As **technician**, register assets. They start in `commissioning` and are *not fit for use*.
3. Record the calibration with an e-signature, then a **QA** user approves it.
4. **QA** sets the qualification to `qualified` and releases the asset to `in_service` (e-signature).
5. Operators scan the asset at the point of use and get **FIT FOR USE** or **DO NOT USE** with reasons.

API docs: `http://127.0.0.1:8000/docs`. Tests: `python -m pytest tests`.

## Barcode support

| Input method | How |
|---|---|
| USB / Bluetooth scanner (keyboard wedge) | Scan into the Scan screen. The field keeps focus and Enter submits. |
| Device camera (tablet, phone, laptop) | Press **Use camera**. It uses the native `BarcodeDetector`, with a ZXing fallback. The page must be on HTTPS or localhost. |
| Serial / RS-232 / USB-CDC scanner | `python -m gmp_assets.serial_bridge --port COM3 --user op1` (needs `pyserial`) |
| Integrations, MES, LIMS | `POST /api/scan {"data": "..."}` |

What the parser understands:

- **Symbologies**: Code 128, Code 39, EAN/UPC, GS1-128, GS1 DataMatrix, GS1 QR and GS1 DataBar. It uses the AIM symbology identifier when the scanner sends one (`]C1`, `]d2`, `]Q3`, ...).
- **GS1 Application Identifiers**: GTIN (01), batch/lot (10), expiry (17), serial (21), SSCC (00), GIAI (8004), GRAI (8003), GLN (414), NHRN/NDC (710-714), and others.
  - Check digits are validated.
  - Dates use the GS1 century rule. `DD=00` means the last day of the month.
- **FNC1/GS separators**: keyboard-wedge scanners often can't send ASCII 29. You can configure them to send `|`, `~`, `<GS>` or `{GS}` instead, and all of these are accepted. The UI also maps `Ctrl+]` to a separator.
- **Human-readable GS1** input is accepted, e.g. `(01)09501101530003(17)271231(10)LOT1`.

How a scan resolves:

1. Asset tag
2. Location code
3. GS1 GIAI, or GTIN + serial, to an asset
4. Otherwise it is shown as a GS1 item (e.g. a reagent), with an **EXPIRED** flag when the expiry date has passed.

A malformed scan, such as one with a bad check digit, is rejected. It is never matched to the wrong record.

**Labels**: `GET /api/assets/{tag}/label.svg` and `/api/locations/{code}/label.svg` produce Code 128 labels. The encoder (`barcode.code128_svg`) also supports GS1-128. Its output was checked against an independent decoder (zxing-cpp).

**Move mode**: scan a location barcode, then scan assets. Each move is recorded in the audit trail.

## GMP / data-integrity controls

| Requirement | Implementation |
|---|---|
| Audit trail (11.10(e), Annex 11 §9) | Every change records who, what, when (server UTC), the old and new values, and the reason. Entries are **SHA-256 hash-chained**. DB triggers block UPDATE/DELETE. `GET /api/audit/verify` detects any tampering. CSV export is available. |
| Electronic signatures (11.50, 11.70, 11.200) | The signer re-enters their password. The signature manifest shows full name, date/time and meaning (performed / approved / rejected / released). Each signature is bound to a hash of the signed record content. |
| Access control (11.10(d), (g)) | Roles are admin, qa, technician, operator and viewer. Permissions are checked on the server. |
| Segregation of duties | Admins cannot approve GMP records. The person who performed a calibration cannot approve it. |
| Credential controls (11.300) | PBKDF2 password hashing, complexity rules, 90-day expiry, no re-use of the last 5 passwords, and a forced change on first login. Lockout after 5 failed attempts, counting both logins and signatures. 15-minute idle timeout. User IDs are never re-used. |
| Operational checks (11.10(f)) | Enforced lifecycle: commissioning → in service ↔ out of service / quarantine → retired. Release needs qualification, current calibration and PM, and no open deviations. |
| Calibration traceability | Reference standards must be registered, in service and within calibration on the date they are used. Otherwise an external certificate reference is required. |
| Out-of-tolerance handling | A failed as-found or as-left result automatically **quarantines** the asset and opens a numbered **deviation** that asks for a product-impact assessment. Closing it needs a QA signature. |
| Contemporaneous / original records | Timestamps come from the server. Activity dates cannot be in the future. Records are never deleted (assets are retired). Edits need a reason and use optimistic locking. |
| Maintenance | PM and corrective work are e-signed. "Requalification required" blocks use until QA requalifies the asset. |

## Configuration (environment variables)

`GMP_DB_PATH`, `GMP_ADMIN_USERNAME`, `GMP_ADMIN_PASSWORD`, `GMP_SESSION_IDLE_MINUTES` (15),
`GMP_MAX_FAILED_LOGINS` (5), `GMP_PASSWORD_MAX_AGE_DAYS` (90), `GMP_PASSWORD_MIN_LENGTH` (10),
`GMP_PBKDF2_ITERATIONS` (310000), `GMP_SITE_NAME`.

For production, run behind TLS, for example `run_server.py --ssl-keyfile ... --ssl-certfile ...` or behind a reverse proxy.

## Validation note

This software provides technical controls. It does not make a site compliant on
its own. Before GMP use it must be validated for its intended use under your
computerized-system validation process (e.g. GAMP 5: URS, risk assessment,
IQ/OQ/PQ, traceability matrix). It also needs:

- SOPs for user administration, backup/restore and periodic audit-trail review
- time synchronization on the server
- regular backups of the SQLite file (including the `-wal` file)
- a documented decision on whether you need LDAP/AD single sign-on, which is not included

The automated test suite (`tests/`) can serve as supporting OQ evidence.

## Layout

```
gmp_assets/
  barcode.py        GS1/AIM parsing, check digits, Code 128 / GS1-128 SVG generation
  services.py       business rules (lifecycle, calibration, deviations, e-signatures)
  audit.py          hash-chained audit trail and verification
  security.py       passwords, sessions, roles/permissions
  db.py             SQLite schema with append-only / no-delete triggers
  api.py            FastAPI REST API + serves the web UI
  serial_bridge.py  serial/COM scanner client
  static/index.html scan station and management UI
tests/              barcode and end-to-end GMP workflow tests
```
