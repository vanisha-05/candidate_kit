from datetime import date, datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError


IST = timezone(timedelta(hours=5, minutes=30))


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def current_month_monday() -> date:
    today = datetime.now(IST).date()
    first_of_month = today.replace(day=1)
    return first_of_month + timedelta(days=(7 - first_of_month.weekday()) % 7)


def ist_time(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def punch(client, path: str, emp_code: str, timestamp: datetime) -> dict:
    response = client.post(
        path,
        json={"emp_code": emp_code, path.rsplit("/", 1)[-1]: epoch_ms(timestamp)},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_punch_in_then_punch_out_flow(api_client):
    client, db = api_client
    day = current_month_monday()
    punch_in = ist_time(day, 9, 28)
    punch_out = ist_time(day, 18, 35)

    opened = punch(client, "/attendance/punch_in", "EMP0001", punch_in)
    closed = punch(client, "/attendance/punch_out", "EMP0001", punch_out)

    assert closed["date"] == day.isoformat()
    assert closed["work_hours"] == pytest.approx(9.12)
    assert closed["late_minutes"] == 0
    assert closed["overtime_minutes"] == 0
    assert closed["half_day"] is False

    stored = db.attendance_logs.find_one({"emp_code": "EMP0001", "date": day.isoformat()})
    assert stored is not None
    assert stored["punch_in"].replace(tzinfo=timezone.utc).timestamp() * 1000 == opened["punch_in"]
    assert stored["punch_out"].replace(tzinfo=timezone.utc).timestamp() * 1000 == closed["punch_out"]
    assert stored["work_hours"] == closed["work_hours"]
    assert stored["late_minutes"] == closed["late_minutes"]
    assert stored["overtime_minutes"] == closed["overtime_minutes"]
    assert stored["half_day"] == closed["half_day"]


def test_late_arrival_with_overtime(api_client):
    client, db = api_client
    day = current_month_monday()

    punch(client, "/attendance/punch_in", "EMP0003", ist_time(day, 10, 5))
    closed = punch(client, "/attendance/punch_out", "EMP0003", ist_time(day, 19, 10))

    assert closed["late_minutes"] == 35
    assert closed["overtime_minutes"] == 40
    assert closed["work_hours"] == pytest.approx(9.08)
    stored = db.attendance_logs.find_one({"emp_code": "EMP0003", "date": day.isoformat()})
    assert stored is not None
    assert stored["late_minutes"] == 35
    assert stored["overtime_minutes"] == 40
    assert stored["work_hours"] == pytest.approx(9.08)


def test_half_day_counts_as_half_present_day(api_client):
    client, _ = api_client
    day = current_month_monday()

    punch(client, "/attendance/punch_in", "EMP0004", ist_time(day, 9, 30))
    closed = punch(client, "/attendance/punch_out", "EMP0004", ist_time(day, 13, 0))
    summary = client.get(
        "/analytics/department_summary",
        params={"department": "Support"},
    )

    assert closed["work_hours"] < 4.5
    assert closed["half_day"] is True
    assert summary.status_code == 200, summary.text
    support = next(
        item for item in summary.json()["items"] if item["department"] == "Support"
    )
    assert support["present_days"] == 0.5


def test_overnight_shift_date_and_overtime(api_client):
    client, db = api_client
    day = current_month_monday()

    punch(client, "/attendance/punch_in", "EMP0005", ist_time(day, 21, 55))
    closed = punch(
        client,
        "/attendance/punch_out",
        "EMP0005",
        ist_time(day + timedelta(days=1), 6, 40),
    )

    assert closed["date"] == day.isoformat()
    assert closed["work_hours"] == pytest.approx(8.75)
    assert closed["overtime_minutes"] == 40
    stored = db.attendance_logs.find_one({"emp_code": "EMP0005", "date": day.isoformat()})
    assert stored is not None
    assert stored["work_hours"] == pytest.approx(8.75)
    assert stored["overtime_minutes"] == 40


def test_manual_correction_recomputes_fields_and_appends_history(api_client):
    client, db = api_client
    day = current_month_monday()

    punch(client, "/attendance/punch_in", "EMP0001", ist_time(day, 10, 5))
    punch(client, "/attendance/punch_out", "EMP0001", ist_time(day, 18, 30))
    correction = client.post(
        "/attendance/correct",
        json={
            "emp_code": "EMP0001",
            "date": day.isoformat(),
            "changes": {"punch_in": epoch_ms(ist_time(day, 9, 28))},
            "reason": "biometric glitch",
            "by": "hr.admin",
        },
    )

    assert correction.status_code == 200, correction.text
    corrected = correction.json()
    assert corrected["late_minutes"] == 0
    assert corrected["work_hours"] == pytest.approx(9.03)
    assert len(corrected["history"]) == 1
    history = corrected["history"][0]
    assert history["by"] == "hr.admin"
    assert history["reason"] == "biometric glitch"
    assert set(history["changes"]) == {"punch_in", "late_minutes", "work_hours"}
    assert history["changes"]["late_minutes"] == {"from": 35, "to": 0}
    assert history["changes"]["work_hours"]["from"] == pytest.approx(8.42)
    assert history["changes"]["work_hours"]["to"] == pytest.approx(9.03)

    stored = db.attendance_logs.find_one({"emp_code": "EMP0001", "date": day.isoformat()})
    assert stored is not None
    assert stored["late_minutes"] == 0
    assert stored["work_hours"] == pytest.approx(9.03)
    assert len(stored["history"]) == 1
    assert stored["history"][0]["changes"]["punch_in"]["from"] is not None
    assert stored["history"][0]["changes"]["punch_in"]["to"] is not None


def test_late_leaderboard_includes_ties_at_limit(api_client):
    client, _ = api_client
    day = current_month_monday()

    for emp_code in ("EMP0001", "EMP0002"):
        punch(client, "/attendance/punch_in", emp_code, ist_time(day, 10, 15))

    response = client.get("/analytics/late_leaderboard", params={"limit": 1})

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["emp_code"] for item in items} == {"EMP0001", "EMP0002"}
    assert [item["rank"] for item in items] == [1, 1]


def test_department_summary_counts_zero_log_employees(api_client):
    client, db = api_client
    day = current_month_monday()
    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0001",
                "date": day.isoformat(),
                "status": "PRESENT",
                "punch_in": ist_time(day, 9, 30).astimezone(timezone.utc),
                "punch_out": None,
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0002",
                "date": day.isoformat(),
                "status": "ABSENT",
                "punch_in": None,
                "punch_out": None,
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
        ]
    )

    response = client.get(
        "/analytics/department_summary",
        params={"department": "Engineering"},
    )

    assert response.status_code == 200, response.text
    engineering = next(
        item
        for item in response.json()["items"]
        if item["department"] == "Engineering"
    )
    assert engineering["headcount"] == 3
    assert engineering["present_days"] == 1
    assert engineering["absent_days"] == 1


def test_unique_indexes_reject_duplicate_employee_and_attendance(api_client):
    _, db = api_client
    employee = db.employees.find_one({"emp_code": "EMP0001"})
    assert employee is not None
    with pytest.raises(DuplicateKeyError):
        db.employees.insert_one(employee)

    attendance = {
        "emp_code": "EMP0001",
        "date": "2026-01-01",
        "status": "ABSENT",
        "punch_in": None,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    db.attendance_logs.insert_one(attendance)
    with pytest.raises(DuplicateKeyError):
        db.attendance_logs.insert_one(attendance)
