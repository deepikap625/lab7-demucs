VERSION=v1
DOCKERUSER=YOUR_DOCKER_USERNAME_HERE

build:
	docker build -f Dockerfile-rest -t demucs-rest:local -t demucs-rest:$(VERSION) .

push:
	docker tag demucs-rest:$(VERSION) $(DOCKERUSER)/demucs-rest:$(VERSION)
	docker push $(DOCKERUSER)/demucs-rest:$(VERSION)
	docker tag demucs-rest:$(VERSION) $(DOCKERUSER)/demucs-rest:latest
	docker push $(DOCKERUSER)/demucs-rest:latest
