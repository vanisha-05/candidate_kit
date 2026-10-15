# Candidate kit

![Coverage](https://codecov.io/gh/vanisha-05/candidate_kit/branch/main/graph/badge.svg)

## Project Status

- CI passing ✅
- Coverage badge 📊
- MongoDB 7 tested 🔒

CI runs integration tests against MongoDB 7 and uploads coverage to Codecov. Badge shows live coverage %.

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Integration tests
```bash
pip install -r requirements-test.txt
python -m pytest
```
Tests use `TEST_MONGO_URI` when set, otherwise a local MongoDB at `localhost:27017`, or a MongoDB 7 test container
when Docker is available. Each test gets an isolated database that is dropped afterward.

## Submit

A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
"Trigger CI run for Codecov" 
"Trigger Codecov upload" 
