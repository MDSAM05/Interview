# Test and Coverage Instructions

## Run All Tests

```
pytest --maxfail=2 --disable-warnings
```

## Run Tests with Coverage Report

```
pytest --cov=problems --cov=common --cov-report=term-missing --disable-warnings
```

- Coverage config: `.coveragerc` (already created)
- Target: At least 80% coverage
- All test files are in `backend-engineer/tests/`

## Notes
- Ensure all services and dependencies (DB, Redis, RabbitMQ) are running for integration tests.
- For full coverage, add more unit tests for utility functions in `common/` if needed.
