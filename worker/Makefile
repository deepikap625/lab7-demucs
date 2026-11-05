VERSION=v1
DOCKERUSER=YOUR_DOCKER_USERNAME_HERE

build:
	docker build -t demucs-worker:local -t demucs-worker:$(VERSION) .
push:
	docker tag demucs-worker:$(VERSION) $(DOCKERUSER)/demucs-worker:$(VERSION)
	docker push $(DOCKERUSER)/demucs-worker:$(VERSION)
	docker tag demucs-worker:$(VERSION) $(DOCKERUSER)/demucs-worker:latest
	docker push $(DOCKERUSER)/demucs-worker:latest
