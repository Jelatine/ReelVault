# Independent S3 protocol peer for integration tests, not a production service.
FROM golang:1.25-alpine AS build
RUN apk add --no-cache git
RUN CGO_ENABLED=0 GOMAXPROCS=2 go install github.com/minio/minio@v0.0.0-20251015172955-9e49d5e7a648
FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=build /go/bin/minio /usr/local/bin/minio
ENTRYPOINT ["minio"]
