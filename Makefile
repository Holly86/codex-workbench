.PHONY: bootstrap backend-test backend-lint backend-typecheck frontend-test frontend-lint frontend-typecheck frontend-build audit verify schemas

bootstrap:
	./scripts/bootstrap-backend.sh
	./scripts/bootstrap-frontend.sh

backend-test:
	cd backend && .venv/bin/pytest

backend-lint:
	cd backend && .venv/bin/ruff check .

backend-typecheck:
	cd backend && .venv/bin/mypy

frontend-test:
	cd frontend && npm test

frontend-lint:
	cd frontend && npm run lint

frontend-typecheck:
	cd frontend && npm run typecheck

frontend-build:
	cd frontend && npm run build

audit:
	./scripts/audit-dependencies.sh

verify:
	./scripts/verify.sh

schemas:
	./scripts/update-app-server-schemas.sh
