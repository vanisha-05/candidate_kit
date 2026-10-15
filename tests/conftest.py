import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app import main


@pytest.fixture(scope="session")
def mongo_client():
    container = None
    uri = os.getenv("TEST_MONGO_URI")

    if uri is None:
        local_client = MongoClient(
            "mongodb://localhost:27017",
            serverSelectionTimeoutMS=1000,
        )
        try:
            local_client.admin.command("ping")
            uri = "mongodb://localhost:27017"
        except PyMongoError:
            local_client.close()
            from testcontainers.community.mongodb import MongoDbContainer

            container = MongoDbContainer("mongo:7.0")
            container.start()
            uri = container.get_connection_url()

    client = MongoClient(uri, serverSelectionTimeoutMS=2000)
    deadline = time.monotonic() + 30
    last_error = None
    while time.monotonic() < deadline:
        try:
            client.admin.command("ping")
            break
        except PyMongoError as exc:
            last_error = exc
            time.sleep(0.5)
    else:
        client.close()
        logs = container.get_logs() if container is not None else None
        if container is not None:
            container.stop()
        pytest.fail(
            f"Could not connect to the test MongoDB: {last_error}. "
            f"Container logs: {logs!r}"
        )

    yield client

    client.close()
    if container is not None:
        container.stop()


@pytest.fixture
def api_client(mongo_client, monkeypatch):
    db_name = f"attendance_api_test_{uuid.uuid4().hex}"
    test_db = mongo_client[db_name]
    monkeypatch.setattr(main, "db", test_db)

    employees = [
        {
            "emp_code": "EMP0001",
            "name": "Asha Rao",
            "email": "asha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2020-01-01",
        },
        {
            "emp_code": "EMP0002",
            "name": "Vikram Shah",
            "email": "vikram@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2020-01-01",
        },
        {
            "emp_code": "EMP0003",
            "name": "Neha Iyer",
            "email": "neha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2020-01-01",
        },
        {
            "emp_code": "EMP0004",
            "name": "Rohit Das",
            "email": "rohit@example.com",
            "department": "Support",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2020-01-01",
        },
        {
            "emp_code": "EMP0005",
            "name": "Meera Nair",
            "email": "meera@example.com",
            "department": "Support",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2020-01-01",
        },
    ]

    try:
        with TestClient(main.app) as client:
            test_db.employees.delete_many({})
            test_db.attendance_logs.delete_many({})
            test_db.employees.insert_many(employees)
            yield client, test_db
    finally:
        mongo_client.drop_database(db_name)
