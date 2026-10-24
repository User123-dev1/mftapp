"""Sync devices and their owners from Microsoft Entra ID into the CMDB.

Works without Intune: it reads the directory's device objects through Microsoft
Graph using the app registration's own credentials (client-credentials flow).
Run it from the "Sync now" button, the built-in daily schedule, or the command
line (``python -m app.entra_sync``) for cron / Windows Task Scheduler.
"""

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import database, sso
from .config import settings
from .models import CIStatus, CIType, ConfigItem, Role, SyncRun, User, utcnow
from .security import NO_PASSWORD

logger = logging.getLogger(__name__)

SOURCE = "entra"
_DEVICE_FIELDS = (
    "id,deviceId,displayName,operatingSystem,operatingSystemVersion,trustType,accountEnabled,"
    "approximateLastSignInDateTime,manufacturer,model"
)
# Computer types an existing hand-entered / imported item may have to be matched by name.
_COMPUTER_TYPES = (CIType.LAPTOP, CIType.DESKTOP, CIType.SERVER, CIType.MOBILE, CIType.OTHER)
_run_lock = threading.Lock()


class SyncError(Exception):
    pass


@dataclass
class SyncResult:
    seen: int = 0
    created: int = 0
    updated: int = 0
    linked: int = 0
    skipped_registered: int = 0
    retired: int = 0
    missing: int = 0
    owners_set: int = 0
    users_created: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.seen} devices in Entra", f"{self.created} created", f"{self.updated} updated"]
        if self.linked:
            parts.append(f"{self.linked} linked to existing items")
        if self.owners_set:
            parts.append(f"{self.owners_set} owners set")
        if self.users_created:
            parts.append(f"{self.users_created} user accounts created")
        if self.skipped_registered:
            parts.append(f"{self.skipped_registered} personal devices skipped")
        if self.missing:
            parts.append(f"{self.missing} no longer in Entra" + (f" ({self.retired} retired)" if self.retired else ""))
        return ", ".join(parts)


def is_configured() -> bool:
    return bool(settings.entra_tenant_id and settings.entra_client_id and settings.entra_client_secret)


# --- Graph client -----------------------------------------------------------------

class GraphClient:
    def __init__(self, http: Optional[httpx.Client] = None):
        self.http = http or httpx.Client(timeout=60)
        self._token: Optional[str] = None

    def _get_token(self) -> str:
        url = f"{settings.entra_authority.rstrip('/')}/{settings.entra_tenant_id}/oauth2/v2.0/token"
        resp = self.http.post(url, data={
            "grant_type": "client_credentials",
            "client_id": settings.entra_client_id,
            "client_secret": settings.entra_client_secret,
            "scope": f"{settings.graph_base_url.rstrip('/')}/.default",
        })
        if resp.status_code != 200:
            raise SyncError(f"Could not get a Graph token ({resp.status_code}): {_error_text(resp)}")
        return resp.json()["access_token"]

    def get_pages(self, path: str, params: dict) -> Iterator[dict]:
        if self._token is None:
            self._token = self._get_token()
        url: Optional[str] = f"{settings.graph_base_url.rstrip('/')}/v1.0{path}"
        while url:
            resp = self._get(url, params)
            data = resp.json()
            yield from data.get("value", [])
            url, params = data.get("@odata.nextLink"), None  # nextLink already carries the query

    def _get(self, url: str, params: Optional[dict]) -> httpx.Response:
        for attempt in range(4):
            resp = self.http.get(url, params=params, headers={"Authorization": f"Bearer {self._token}"})
            if resp.status_code in (429, 503, 504) and attempt < 3:  # throttled: honour Retry-After
                time.sleep(min(int(resp.headers.get("Retry-After", "5")), 60))
                continue
            if resp.status_code == 403:
                raise SyncError("Graph denied access. Grant the app Device.Read.All and User.Read.All "
                                "(application permissions) and click 'Grant admin consent'.")
            if resp.status_code != 200:
                raise SyncError(f"Graph request failed ({resp.status_code}): {_error_text(resp)}")
            return resp
        raise SyncError("Graph kept throttling the request; try again later.")

    def devices(self) -> Iterator[dict]:
        return self.get_pages("/devices", {"$select": _DEVICE_FIELDS, "$expand": "registeredOwners", "$top": "999"})


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("message", str(err))
        return body.get("error_description") or str(err or body)
    except ValueError:
        return resp.text[:300]


# --- Mapping ------------------------------------------------------------------------

def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def _type_for(os_name: str) -> CIType:
    os_lower = (os_name or "").lower()
    if any(m in os_lower for m in ("ios", "ipados", "android")):
        return CIType.MOBILE
    if "server" in os_lower:
        return CIType.SERVER
    try:
        return CIType(settings.entra_sync_default_type)
    except ValueError:
        return CIType.LAPTOP


def _owner_email(device: dict) -> Optional[str]:
    for owner in device.get("registeredOwners") or []:
        email = owner.get("mail") or owner.get("userPrincipalName")
        if email and "#EXT#" not in email:  # skip guest UPNs
            return email.strip().lower()
    return None


def _owner_name(device: dict) -> Optional[str]:
    for owner in device.get("registeredOwners") or []:
        if owner.get("displayName"):
            return owner["displayName"]
    return None


def apply_devices(db: Session, devices: Iterator[dict]) -> SyncResult:
    """Create, link or update CMDB items from Graph device objects. Caller commits."""
    result = SyncResult()
    now = utcnow()
    allow_placeholders = bool(sso.providers())
    seen_ids: set[str] = set()

    for device in devices:
        result.seen += 1
        object_id, name = device.get("id"), (device.get("displayName") or "").strip()
        if not object_id or not name:
            continue
        if device.get("trustType") == "Workplace" and not settings.entra_sync_include_registered:
            result.skipped_registered += 1
            continue
        seen_ids.add(object_id)

        ci = db.scalar(select(ConfigItem).where(ConfigItem.entra_object_id == object_id))
        if ci is None:
            # Link to an item already in the CMDB (CSV import / manual) with the same name.
            candidates = db.scalars(select(ConfigItem).where(
                func.lower(ConfigItem.name) == name.lower(),
                ConfigItem.entra_object_id.is_(None),
                ConfigItem.ci_type.in_(_COMPUTER_TYPES),
            )).all()
            if len(candidates) == 1:
                ci = candidates[0]
                result.linked += 1
            elif len(candidates) > 1:
                result.warnings.append(f"{name}: {len(candidates)} items share this name; created a new one.")
        if ci is None:
            ci = ConfigItem(name=name, ci_type=_type_for(device.get("operatingSystem")), status=CIStatus.IN_USE)
            db.add(ci)
            result.created += 1
        elif ci.entra_object_id:
            result.updated += 1

        ci.entra_object_id = object_id
        ci.name = name
        os_text = " ".join(p for p in (device.get("operatingSystem"), device.get("operatingSystemVersion")) if p)
        if os_text:
            ci.version = os_text
        # Fill hardware details only where the CMDB has none (CSV/vendor data wins).
        ci.manufacturer = ci.manufacturer or device.get("manufacturer") or None
        ci.model = ci.model or device.get("model") or None
        ci.entra_last_sign_in = _parse_time(device.get("approximateLastSignInDateTime")) or ci.entra_last_sign_in
        ci.entra_synced_at = now
        if device.get("accountEnabled") is False and ci.status == CIStatus.IN_USE:
            ci.status = CIStatus.MAINTENANCE
            result.warnings.append(f"{name}: disabled in Entra; marked In Maintenance.")

        email = _owner_email(device)
        if email and (ci.assigned_user is None or ci.assigned_user.email != email):
            owner = db.scalar(select(User).where(User.email == email))
            if owner is None and allow_placeholders:
                owner = User(email=email, full_name=_owner_name(device) or email.split("@")[0],
                             password_hash=NO_PASSWORD, role=Role.REQUESTER, is_active=True)
                db.add(owner)
                result.users_created += 1
            if owner is not None:
                ci.assigned_user = owner
                result.owners_set += 1
            else:
                result.warnings.append(f"{name}: owner {email} has no account here.")
        db.flush()

    # Previously synced devices that are gone from Entra.
    gone = db.scalars(select(ConfigItem).where(
        ConfigItem.entra_object_id.is_not(None), ConfigItem.status != CIStatus.RETIRED
    )).all()
    for ci in gone:
        if ci.entra_object_id in seen_ids:
            continue
        result.missing += 1
        if settings.entra_sync_retire_missing:
            ci.status = CIStatus.RETIRED
            result.retired += 1
    return result


# --- Running ---------------------------------------------------------------------------

def run_sync(trigger: str, client: Optional[GraphClient] = None) -> SyncRun:
    """Run one sync and record it. Never raises; failures are stored on the SyncRun."""
    db = database.SessionLocal()
    run = SyncRun(source=SOURCE, trigger=trigger, status="running")
    db.add(run)
    db.commit()
    if not _run_lock.acquire(blocking=False):
        run.status, run.error, run.finished_at = "failed", "Another sync is already running.", utcnow()
        db.commit()
        db.close()
        return run
    try:
        if not is_configured():
            raise SyncError("Set ENTRA_TENANT_ID, ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET first.")
        result = apply_devices(db, (client or GraphClient()).devices())
        run.status = "success"
        data = asdict(result)
        data["warnings"] = data["warnings"][:200]
        run.summary = json.dumps({"text": result.summary(), **data})
        db.commit()
        logger.info("Entra sync: %s", result.summary())
    except httpx.HTTPError as exc:
        db.rollback()
        run = db.get(SyncRun, run.id)
        run.status = "failed"
        run.error = (f"Could not reach Microsoft ({type(exc).__name__}: {exc}). Check that this server can reach "
                     f"{urlsplit(settings.entra_authority).hostname} and {urlsplit(settings.graph_base_url).hostname} "
                     f"over HTTPS (firewall / proxy).")
    except Exception as exc:
        db.rollback()
        run = db.get(SyncRun, run.id)
        run.status = "failed"
        run.error = str(exc) if isinstance(exc, SyncError) else f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, SyncError):
            logger.exception("Entra sync failed")
    finally:
        run.finished_at = utcnow()
        db.commit()
        db.close()
        _run_lock.release()
    return run


def last_runs(db: Session, limit: int = 20) -> list[SyncRun]:
    return list(db.scalars(select(SyncRun).where(SyncRun.source == SOURCE).order_by(SyncRun.id.desc()).limit(limit)))


class DailyScheduler:
    """Runs the sync once a day at ENTRA_SYNC_HOUR (UTC) in a background thread.

    With several web worker processes each would run it; in that case leave
    ENTRA_SYNC_ENABLED off and schedule ``python -m app.entra_sync`` instead.
    """

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="entra-sync", daemon=True)
        self._thread.start()
        logger.info("Entra device sync scheduled daily at %02d:00 UTC", settings.entra_sync_hour)

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def seconds_until_next(now: datetime) -> float:
        target = now.replace(hour=settings.entra_sync_hour % 24, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _loop(self) -> None:
        while not self._stop.wait(self.seconds_until_next(datetime.now(timezone.utc).replace(tzinfo=None))):
            run_sync("schedule")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database.init_db()
    finished = run_sync("cli")
    if finished.status == "success":
        print(json.loads(finished.summary)["text"])
    else:
        print(f"Sync failed: {finished.error}")
        raise SystemExit(1)
