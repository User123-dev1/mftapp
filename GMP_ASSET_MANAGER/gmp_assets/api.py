"""REST API and web UI host."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .security import ROLE_PERMISSIONS
from .services import INTERVAL_UNITS, PM_EVENTS, WORK_TYPES, AuthError, Forbidden, GMPAssetService, GMPError

logger = logging.getLogger("gmp_assets")
STATIC_DIR = Path(__file__).parent / "static"


# ----------------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class Signature(BaseModel):
    """Electronic signature: the signer re-enters their password."""
    password: str


class UserIn(BaseModel):
    username: str
    full_name: str
    role: str
    temporary_password: str


class UserUpdateIn(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None
    unlock: bool = False
    temporary_password: Optional[str] = None
    reason: str


class LocationIn(BaseModel):
    code: str
    name: str
    area_class: Optional[str] = None


class CompanyIn(BaseModel):
    name: str
    site_name: str
    address: Optional[str] = None
    asset_prefix: str
    reason: Optional[str] = None


class AssetIn(BaseModel):
    asset_tag: Optional[str] = Field(default=None, description="Leave empty to generate the next asset ID")
    initial_state: str = Field(default="inactive", description="active | inactive")
    description: str
    category: str
    gmp_impact: str = Field(description="direct | indirect | none")
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    gtin: Optional[str] = None
    department: Optional[str] = None
    location_code: Optional[str] = None
    is_reference_standard: bool = False
    calibration_required: bool = False
    calibration_interval_days: Optional[int] = None
    pm_required: bool = False
    pm_interval_days: Optional[int] = None
    reason: Optional[str] = None


class AssetUpdateIn(BaseModel):
    changes: dict
    reason: str
    expected_version: Optional[int] = None


class MoveIn(BaseModel):
    location_code: str
    reason: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    reason: str
    signature: Optional[Signature] = None


class QualificationIn(BaseModel):
    qualification_status: str
    reason: str
    signature: Signature


class CalibrationIn(BaseModel):
    performed_on: str
    as_found: str
    as_left: str
    standards: List[str] = []
    certificate_ref: Optional[str] = None
    notes: Optional[str] = None
    signature: Signature


class ReviewIn(BaseModel):
    decision: str
    comment: Optional[str] = None
    signature: Signature


class MaintenanceIn(BaseModel):
    maintenance_type: str
    performed_on: str
    description: str
    requalification_required: bool = False
    signature: Signature


class PMScheduleIn(BaseModel):
    title: str
    instructions: Optional[str] = None
    work_type: str = "preventive"
    trigger_type: str = Field(default="time", description="time | event")
    interval_value: int = Field(description="Repeat interval (time) or 'complete within' period (event)")
    interval_unit: str = "days"
    event: Optional[str] = None
    lead_days: int = 0
    blocks_use: bool = True
    first_due_on: Optional[str] = None


class PMScheduleUpdateIn(BaseModel):
    changes: dict
    reason: str


class WorkOrderIn(BaseModel):
    work_type: str = "preventive"
    title: str
    instructions: Optional[str] = None
    due_on: str
    blocks_use: bool = False


class CompleteWorkOrderIn(BaseModel):
    performed_on: str
    notes: str
    requalification_required: bool = False
    signature: Signature


class CancelIn(BaseModel):
    reason: str


class IssueIn(BaseModel):
    description: str


class CloseDeviationIn(BaseModel):
    comment: str
    signature: Signature


class ScanIn(BaseModel):
    data: str


def _pw(sig: Optional[Signature]) -> Optional[str]:
    return sig.password if sig else None


# ----------------------------------------------------------------------------
# Application factory
# ----------------------------------------------------------------------------

def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    service = GMPAssetService(Database(settings.db_path), settings)
    generated = service.ensure_admin(os.environ.get("GMP_ADMIN_USERNAME", "admin"), os.environ.get("GMP_ADMIN_PASSWORD"))
    if generated:
        logger.warning("Initial administrator 'admin' created with temporary password: %s (must be changed at first login)", generated)
        print(f"\n*** Initial administrator 'admin' temporary password: {generated} ***\n")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def work_order_generator():
            while True:
                try:
                    created = await asyncio.to_thread(service.generate_due_work_orders)
                    if created:
                        logger.info("Scheduled work orders raised: %s", ", ".join(created))
                except Exception:  # keep the scheduler alive; errors are logged
                    logger.exception("Work order generation failed")
                await asyncio.sleep(3600)

        task = asyncio.create_task(work_order_generator())
        yield
        task.cancel()

    app = FastAPI(title="GMP Asset Management", version="1.1.0", lifespan=lifespan,
                  description="GMP-regulated equipment & instrument asset management with barcode scanning "
                              "(21 CFR Part 11 / EU Annex 11).")
    app.state.service = service
    app.state.settings = settings

    @app.exception_handler(GMPError)
    async def _gmp_error(_: Request, exc: GMPError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    def _token(authorization: Optional[str] = Header(default=None)) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("Not authenticated")
        return authorization[7:].strip()

    def any_user(token: str = Depends(_token)) -> dict:
        return service.session_user(token)

    def current_user(user: dict = Depends(any_user)) -> dict:
        if user["must_change_password"]:
            raise Forbidden("Password change required before continuing")
        return user

    # -- UI ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        # never cache the UI, so a server update is picked up on the next page load
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "site": settings.site_name}

    # -- Auth ----------------------------------------------------------------
    @app.post("/api/auth/login")
    def login(body: LoginIn):
        return service.login(body.username, body.password)

    @app.post("/api/auth/logout")
    def logout(token: str = Depends(_token), user: dict = Depends(any_user)):
        service.logout(token, user)
        return {"ok": True}

    @app.post("/api/auth/change-password")
    def change_password(body: ChangePasswordIn, user: dict = Depends(any_user)):
        service.change_password(user, body.old_password, body.new_password)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(any_user)):
        return dict(user, permissions=sorted(ROLE_PERMISSIONS.get(user["role"], ())))

    # -- Company -------------------------------------------------------------
    @app.get("/api/company/public")
    def company_public():
        c = service.get_company()
        return {"configured": c is not None, "name": c["name"] if c else None, "site_name": c["site_name"] if c else None}

    @app.get("/api/company")
    def company(user: dict = Depends(any_user)):
        return service.get_company()

    @app.put("/api/company")
    def save_company(body: CompanyIn, user: dict = Depends(current_user)):
        return service.save_company(user, name=body.name, site_name=body.site_name, address=body.address,
                                    asset_prefix=body.asset_prefix, reason=body.reason)

    # -- Users ---------------------------------------------------------------
    @app.get("/api/users")
    def users(user: dict = Depends(current_user)):
        return service.list_users(user)

    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, user: dict = Depends(current_user)):
        return service.create_user(user, body.username, body.full_name, body.role, body.temporary_password)

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UserUpdateIn, user: dict = Depends(current_user)):
        return service.update_user(user, user_id, role=body.role, active=body.active, unlock=body.unlock,
                                   temporary_password=body.temporary_password, reason=body.reason)

    # -- Locations -----------------------------------------------------------
    @app.get("/api/locations")
    def locations(user: dict = Depends(current_user)):
        return service.list_locations(user)

    @app.post("/api/locations", status_code=201)
    def create_location(body: LocationIn, user: dict = Depends(current_user)):
        return service.create_location(user, body.code, body.name, body.area_class)

    @app.get("/api/locations/{code}/label.svg")
    def location_label(code: str, user: dict = Depends(current_user)):
        return Response(service.location_label_svg(user, code), media_type="image/svg+xml")

    # -- Scanning ------------------------------------------------------------
    @app.post("/api/scan")
    def scan(body: ScanIn, user: dict = Depends(current_user)):
        return service.scan(user, body.data)

    # -- Assets --------------------------------------------------------------
    @app.get("/api/assets")
    def assets(status: Optional[str] = None, location: Optional[str] = None, q: Optional[str] = None,
               user: dict = Depends(current_user)):
        return service.list_assets(user, status=status, location=location, search=q)

    @app.post("/api/assets", status_code=201)
    def create_asset(body: AssetIn, user: dict = Depends(current_user)):
        return service.create_asset(user, body.model_dump())

    @app.get("/api/assets/{tag}")
    def get_asset(tag: str, user: dict = Depends(current_user)):
        return service.get_asset(user, tag)

    @app.patch("/api/assets/{tag}")
    def update_asset(tag: str, body: AssetUpdateIn, user: dict = Depends(current_user)):
        return service.update_asset(user, tag, body.changes, body.reason, body.expected_version)

    @app.get("/api/assets/{tag}/fitness")
    def fitness(tag: str, user: dict = Depends(current_user)):
        return service.fitness(user, tag)

    @app.get("/api/assets/{tag}/label.svg")
    def asset_label(tag: str, user: dict = Depends(current_user)):
        return Response(service.asset_label_svg(user, tag), media_type="image/svg+xml")

    @app.post("/api/assets/{tag}/move")
    def move(tag: str, body: MoveIn, user: dict = Depends(current_user)):
        return service.move_asset(user, tag, body.location_code, body.reason)

    @app.post("/api/assets/{tag}/status")
    def status(tag: str, body: StatusIn, user: dict = Depends(current_user)):
        return service.change_status(user, tag, body.status, body.reason, _pw(body.signature))

    @app.post("/api/assets/{tag}/qualification")
    def qualification(tag: str, body: QualificationIn, user: dict = Depends(current_user)):
        return service.set_qualification(user, tag, body.qualification_status, body.reason, _pw(body.signature))

    @app.post("/api/assets/{tag}/calibrations", status_code=201)
    def record_calibration(tag: str, body: CalibrationIn, user: dict = Depends(current_user)):
        return service.record_calibration(
            user, tag, performed_on=body.performed_on, as_found=body.as_found, as_left=body.as_left,
            standards=body.standards, certificate_ref=body.certificate_ref, notes=body.notes, password=_pw(body.signature))

    @app.post("/api/assets/{tag}/maintenance", status_code=201)
    def record_maintenance(tag: str, body: MaintenanceIn, user: dict = Depends(current_user)):
        return service.record_maintenance(
            user, tag, maintenance_type=body.maintenance_type, performed_on=body.performed_on,
            description=body.description, requalification_required=body.requalification_required, password=_pw(body.signature))

    @app.post("/api/assets/{tag}/issues", status_code=201)
    def report_issue(tag: str, body: IssueIn, user: dict = Depends(current_user)):
        return service.report_issue(user, tag, body.description)

    # -- PM schedules, triggers and work orders ------------------------------
    @app.get("/api/pm/options")
    def pm_options(user: dict = Depends(current_user)):
        return {"events": PM_EVENTS, "work_types": WORK_TYPES, "interval_units": INTERVAL_UNITS}

    @app.post("/api/assets/{tag}/pm-schedules", status_code=201)
    def create_pm_schedule(tag: str, body: PMScheduleIn, user: dict = Depends(current_user)):
        return service.create_pm_schedule(user, tag, body.model_dump())

    @app.patch("/api/pm-schedules/{schedule_id}")
    def update_pm_schedule(schedule_id: int, body: PMScheduleUpdateIn, user: dict = Depends(current_user)):
        return service.update_pm_schedule(user, schedule_id, body.changes, body.reason)

    @app.post("/api/assets/{tag}/work-orders", status_code=201)
    def create_work_order(tag: str, body: WorkOrderIn, user: dict = Depends(current_user)):
        return service.create_work_order(user, tag, work_type=body.work_type, title=body.title,
                                         instructions=body.instructions, due_on=body.due_on, blocks_use=body.blocks_use)

    @app.get("/api/work-orders")
    def work_orders(status: Optional[str] = None, asset: Optional[str] = None, user: dict = Depends(current_user)):
        return service.list_work_orders(user, status, asset)

    @app.post("/api/work-orders/{number}/complete")
    def complete_work_order(number: str, body: CompleteWorkOrderIn, user: dict = Depends(current_user)):
        return service.complete_work_order(user, number, performed_on=body.performed_on, notes=body.notes,
                                           requalification_required=body.requalification_required, password=_pw(body.signature))

    @app.post("/api/work-orders/{number}/cancel")
    def cancel_work_order(number: str, body: CancelIn, user: dict = Depends(current_user)):
        return service.cancel_work_order(user, number, body.reason)

    # -- Calibrations / deviations -------------------------------------------
    @app.get("/api/calibrations")
    def calibrations(status: Optional[str] = None, user: dict = Depends(current_user)):
        return service.list_calibrations(user, status)

    @app.get("/api/calibrations/{cal_id}")
    def calibration(cal_id: int, user: dict = Depends(current_user)):
        return service.get_calibration(user, cal_id)

    @app.post("/api/calibrations/{cal_id}/review")
    def review(cal_id: int, body: ReviewIn, user: dict = Depends(current_user)):
        return service.review_calibration(user, cal_id, body.decision, body.comment, _pw(body.signature))

    @app.get("/api/deviations")
    def deviations(status: Optional[str] = None, user: dict = Depends(current_user)):
        return service.list_deviations(user, status)

    @app.post("/api/deviations/{number}/close")
    def close_deviation(number: str, body: CloseDeviationIn, user: dict = Depends(current_user)):
        return service.close_deviation(user, number, body.comment, _pw(body.signature))

    # -- Reports & audit -----------------------------------------------------
    @app.get("/api/reports/due")
    def due(days: int = 30, user: dict = Depends(current_user)):
        return service.due_report(user, days)

    @app.get("/api/audit")
    def audit_trail(entity_type: Optional[str] = None, entity_id: Optional[str] = None, username: Optional[str] = None,
                    limit: int = 200, offset: int = 0, user: dict = Depends(current_user)):
        return service.audit_entries(user, entity_type=entity_type, entity_id=entity_id, username=username,
                                     limit=limit, offset=offset)

    @app.get("/api/audit/verify")
    def audit_verify(user: dict = Depends(current_user)):
        return service.verify_audit(user)

    @app.get("/api/audit/export.csv")
    def audit_export(entity_type: Optional[str] = None, entity_id: Optional[str] = None, username: Optional[str] = None,
                     user: dict = Depends(current_user)):
        csv_text = service.audit_csv(user, entity_type=entity_type, entity_id=entity_id, username=username)
        return PlainTextResponse(csv_text, media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=audit_trail.csv"})

    return app
