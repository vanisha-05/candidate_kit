"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import AliasChoices, BaseModel, Field, StrictInt
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def ensure_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="unique_employee_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="unique_employee_attendance_date",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in: datetime, shift_start: str, shift_date: Optional[date] = None
) -> int:
    h, m = map(int, shift_start.split(":"))
    start = datetime.combine(
        shift_date or punch_in.date(),
        time(hour=h, minute=m),
        tzinfo=punch_in.tzinfo,
    )
    elapsed_seconds = (punch_in - start).total_seconds()
    return int(elapsed_seconds // 60) if elapsed_seconds > 10 * 60 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    hours = Decimal(str((punch_out - punch_in).total_seconds())) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
    punch_out: datetime, shift_end: str, attendance_date: str, shift_start: str
) -> int:
    end_day = date.fromisoformat(attendance_date)
    if tuple(map(int, shift_end.split(":"))) <= tuple(map(int, shift_start.split(":"))):
        end_day += timedelta(days=1)
    h, m = map(int, shift_end.split(":"))
    end = datetime.combine(end_day, time(hour=h, minute=m), tzinfo=IST)
    elapsed_seconds = (punch_out - end).total_seconds()
    return int(elapsed_seconds // 60) if elapsed_seconds >= 30 * 60 else 0


def as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).astimezone(IST)
    return value.astimezone(IST)


def epoch_millis(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def attendance_api_record(doc: dict) -> dict:
    record = {key: value for key, value in doc.items() if key != "_id"}
    record["punch_in"] = epoch_millis(record.get("punch_in"))
    record["punch_out"] = epoch_millis(record.get("punch_out"))
    record.setdefault("work_hours", None)
    record.setdefault("late_minutes", 0)
    record.setdefault("overtime_minutes", 0)
    record.setdefault("half_day", False)
    history = []
    for entry in record.get("history", []):
        api_entry = {**entry, "at": epoch_millis(entry["at"])}
        changes = {}
        for field, change in entry.get("changes", {}).items():
            values = dict(change)
            if field in ("punch_in", "punch_out"):
                values["from"] = epoch_millis(values.get("from"))
                values["to"] = epoch_millis(values.get("to"))
            changes[field] = values
        api_entry["changes"] = changes
        history.append(api_entry)
    record["history"] = history
    return record


def parse_epoch_millis(value: int) -> datetime:
    return datetime.fromtimestamp(value // 1000, tz=timezone.utc).astimezone(IST)


def attendance_day_for(punch_in: datetime, employee: dict) -> date:
    punch_in = as_ist(punch_in)
    shift_start = tuple(map(int, employee["shift_start"].split(":")))
    shift_end = tuple(map(int, employee["shift_end"].split(":")))
    day = punch_in.date()
    if shift_end <= shift_start and (punch_in.hour, punch_in.minute) < shift_end:
        day -= timedelta(days=1)
    return day


def calculate_derived_fields(doc: dict, employee: dict, attendance_date: str) -> dict:
    if doc["status"] not in ("PRESENT", "WFH", "ON_DUTY"):
        return {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }
    punch_in = as_ist(doc["punch_in"])
    punch_out = as_ist(doc["punch_out"]) if doc.get("punch_out") is not None else None
    derived = {
        "late_minutes": compute_late_minutes(
            punch_in, employee["shift_start"], date.fromisoformat(attendance_date)
        ),
        "work_hours": None,
        "overtime_minutes": 0,
        "half_day": False,
    }
    if punch_out is not None:
        derived["work_hours"] = compute_work_hours(punch_in, punch_out)
        derived["overtime_minutes"] = compute_overtime(
            punch_out, employee["shift_end"], attendance_date, employee["shift_start"]
        )
        derived["half_day"] = derived["work_hours"] < 4.5
    return derived


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
        validation_alias=AliasChoices("punched_at", "punch_in"),
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: StrictInt = Field(
        validation_alias=AliasChoices("punched_at", "punch_out"),
        ge=100000000000,
        le=4102444800000,
    )


class CorrectionIn(BaseModel):
    emp_code: str
    date: date
    changes: Optional[dict] = None
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None
    punch_in: Optional[StrictInt] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    punch_out: Optional[StrictInt] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(
        min_length=1,
        max_length=50,
        validation_alias=AliasChoices("regularized_by", "by"),
    )


class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None
    punch_in: Optional[StrictInt] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    punch_out: Optional[StrictInt] = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(
        min_length=1,
        max_length=50,
        validation_alias=AliasChoices("regularized_by", "by"),
    )


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "emp_code already exists") from exc
    doc.pop("_id", None)
    doc["created_at"] = epoch_millis(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = page * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201, operation_id="punchIn")
@app.post("/attendance/punch_in", status_code=201, operation_id="punchInUnderscore")
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "unknown employee")

    if body.punched_at is None:
        ts = datetime.now(IST).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(body.punched_at // 1000, tz=timezone.utc).astimezone(IST)

    shift_start = tuple(map(int, emp["shift_start"].split(":")))
    shift_end = tuple(map(int, emp["shift_end"].split(":")))
    attendance_day = ts.date()
    if shift_end <= shift_start and (ts.hour, ts.minute) < shift_end:
        attendance_day -= timedelta(days=1)
    d = attendance_day.isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], attendance_day),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "already punched in for this date") from exc
    return attendance_api_record(doc)


@app.post("/attendance/punch-out", status_code=200, operation_id="punchOut")
@app.post("/attendance/punch_out", status_code=200, operation_id="punchOutUnderscore")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "unknown employee")

    punched_at = parse_epoch_millis(body.punched_at)
    query = {
        "emp_code": body.emp_code,
        "status": {"$in": ["PRESENT", "WFH", "ON_DUTY"]},
        "punch_in": {"$lte": punched_at},
    }
    record = db.attendance_logs.find_one(
        {**query, "punch_out": None},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        closed_record = db.attendance_logs.find_one(
            {**query, "punch_out": {"$ne": None}},
            sort=[("punch_in", DESCENDING)],
        )
        if closed_record is not None:
            raise HTTPException(409, "already punched out")
        raise HTTPException(404, "no punch-in found")

    punch_in_time = as_ist(record["punch_in"])
    elapsed = punched_at - punch_in_time
    if elapsed <= timedelta(0) or elapsed > timedelta(hours=24):
        raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    attendance_date = record["date"]
    derived = calculate_derived_fields(
        {**record, "punch_out": punched_at}, employee, attendance_date
    )
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_in": record["punch_in"],
            "punch_out": None,
        },
        {"$set": {"punch_out": punched_at, **derived}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return attendance_api_record(updated)


def apply_correction(body: CorrectionIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "unknown employee")
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "date": body.date.isoformat()}
    )
    if record is None:
        raise HTTPException(404, "attendance record not found")

    requested = dict(body.changes or {})
    for field in ("status", "punch_in", "punch_out"):
        if field in body.model_fields_set:
            requested[field] = getattr(body, field)
    invalid_fields = set(requested) - {"status", "punch_in", "punch_out"}
    if invalid_fields:
        raise HTTPException(422, f"unsupported correction fields: {sorted(invalid_fields)}")
    if not requested:
        raise HTTPException(422, "at least one correction field is required")
    if "status" in requested and requested["status"] not in (
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ):
        raise HTTPException(422, "invalid status")
    for field in ("punch_in", "punch_out"):
        if field in requested:
            if requested[field] is None:
                raise HTTPException(422, f"{field} cannot be null")
            value = requested[field]
            if type(value) is not int or not 100000000000 <= value <= 4102444800000:
                raise HTTPException(422, f"{field} must be epoch milliseconds")
            requested[field] = parse_epoch_millis(value)

    corrected = {
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
    }
    corrected.update(requested)
    if corrected["status"] in ("ABSENT", "LEAVE"):
        if "punch_in" in requested or "punch_out" in requested:
            raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
        corrected["punch_in"] = None
        corrected["punch_out"] = None
    elif corrected["punch_in"] is None:
        raise HTTPException(422, "presence status requires punch_in")
    else:
        if attendance_day_for(corrected["punch_in"], employee) != body.date:
            raise HTTPException(422, "punch_in must belong to the attendance date")
        if corrected["punch_out"] is not None:
            elapsed = as_ist(corrected["punch_out"]) - as_ist(corrected["punch_in"])
            if elapsed <= timedelta(0) or elapsed > timedelta(hours=24):
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    derived = calculate_derived_fields(corrected, employee, body.date.isoformat())
    changes = {}
    updates = {}
    for field in ("status", "punch_in", "punch_out", *derived.keys()):
        old_value = record.get(
            field,
            {
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
            }.get(field),
        )
        new_value = corrected[field] if field in corrected else derived[field]
        if field in derived:
            new_value = derived[field]
        if field in ("punch_in", "punch_out"):
            if old_value is not None:
                old_value = as_ist(old_value)
            if new_value is not None:
                new_value = as_ist(new_value)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
            updates[field] = new_value
    if not changes:
        raise HTTPException(422, "correction does not change the record")

    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    history_filter = (
        {"history": record["history"]}
        if "history" in record
        else {"history": {"$exists": False}}
    )
    compare_filter = {"_id": record["_id"], **history_filter}
    for field in (
        "status",
        "punch_in",
        "punch_out",
        "late_minutes",
        "work_hours",
        "overtime_minutes",
        "half_day",
    ):
        compare_filter[field] = (
            record[field] if field in record else {"$exists": False}
        )
    updated = db.attendance_logs.find_one_and_update(
        compare_filter,
        {"$set": updates, "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "attendance record changed concurrently")
    return attendance_api_record(updated)


@app.post("/attendance/correct", status_code=200, operation_id="correctAttendance")
def correct_attendance(body: CorrectionIn):
    return apply_correction(body)


@app.patch("/attendance/{emp_code}/{attendance_date}", status_code=200)
def regularize_attendance(emp_code: str, attendance_date: date, body: RegularizeIn):
    payload = body.model_dump(exclude_unset=True)
    payload.update({"emp_code": emp_code, "date": attendance_date})
    return apply_correction(CorrectionIn(**payload))


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


def month_bounds(month: str) -> tuple[str, str, str]:
    first = date.fromisoformat(f"{month}-01")
    if first.month == 12:
        next_month = date(first.year + 1, 1, 1)
    else:
        next_month = date(first.year, first.month + 1, 1)
    return first.isoformat(), next_month.isoformat(), (next_month - timedelta(days=1)).isoformat()


@app.get("/analytics/leaderboard/late", operation_id="lateLeaderboard")
@app.get("/analytics/late_leaderboard", operation_id="lateLeaderboardUnderscore")
def late_leaderboard(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    month = month or datetime.now(IST).strftime("%Y-%m")
    start, end, _ = month_bounds(month)
    pipeline = [
        {
            "$match": {
                "date": {"$gte": start, "$lt": end},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend(
        [
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "employee.emp_code": 1}},
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$employee.emp_code",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/summary", operation_id="departmentSummary")
@app.get("/analytics/department_summary", operation_id="departmentSummaryUnderscore")
def department_summary(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    month = month or datetime.now(IST).strftime("%Y-%m")
    start, end, last_day = month_bounds(month)
    employee_match = {"joined_on": {"$lte": last_day}}
    if department is not None:
        employee_match["department"] = department
    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", start]},
                                    {"$lt": ["$date", end]},
                                ]
                            }
                        }
                    }
                ],
                "as": "log",
            }
        },
        {"$unwind": {"path": "$log", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$department",
                "employee_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": [{"$ifNull": ["$log.date", None]}, None]},
                                    {
                                        "$in": [
                                            "$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": {
                                                            "$ifNull": [
                                                                "$log.date",
                                                                "1970-01-01",
                                                            ]
                                                        }
                                                    }
                                                }
                                            },
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$log.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "absent_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$log.status", "ABSENT"]},
                            1,
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": [{"$ifNull": ["$log.work_hours", None]}, None]},
                                ]
                            },
                            "$log.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": [{"$ifNull": ["$log.work_hours", None]}, None]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$log.late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": {"$ifNull": ["$log.late_minutes", 0]}},
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$log.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$log.status", "ON_DUTY"]}, 1, 0]}
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employee_codes"},
                "present_days": 1,
                "absent_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]
    items = list(db.employees.aggregate(pipeline))
    for item in items:
        if item["avg_work_hours"] is not None:
            average = Decimal(str(item["avg_work_hours"]))
            item["avg_work_hours"] = float(
                average.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
    return {"month": month, "items": items}


@app.get("/analytics/overtime", operation_id="overtimeSummary")
def overtime_summary(month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    lookup_pipeline = []
    if month is not None:
        start, end, _ = month_bounds(month)
        lookup_pipeline.append(
            {"$match": {"date": {"$gte": start, "$lt": end}}}
        )
    lookup_pipeline.append(
        {
            "$group": {
                "_id": None,
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        }
    )
    pipeline = [
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$emp_code", "$$code"]}}},
                    *lookup_pipeline,
                ],
                "as": "overtime",
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": 1,
                "name": 1,
                "department": 1,
                "total_overtime_minutes": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$overtime.total_overtime_minutes", 0]},
                        0,
                    ]
                },
            }
        },
        {"$sort": {"total_overtime_minutes": -1, "emp_code": 1}},
    ]
    result = {"items": list(db.employees.aggregate(pipeline))}
    if month is not None:
        result["month"] = month
    return result


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
