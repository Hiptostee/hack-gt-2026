.PHONY: build up camera-mac stop logs check shell clean

build:
	docker compose build

up:
	./scripts/start.sh

camera-mac:
	./scripts/start-camera-macos.sh

stop:
	docker compose down

logs:
	docker compose logs -f mapper

check:
	./scripts/check.sh

shell:
	docker compose exec mapper bash

clean:
	docker compose down --remove-orphans
