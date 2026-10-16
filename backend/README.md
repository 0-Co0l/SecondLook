# Backend

FastAPI service for the SecondLook tax return review workflow. See the repository README for setup.

- App factory: `uvicorn app.main:create_app --factory`
- Production entry point (migrations + server): `python -m app`
- Migrations: `alembic upgrade head`; new migration: `alembic revision --autogenerate -m "..."`
