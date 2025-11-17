# syntax=docker/dockerfile:1

# Build stage
FROM golang:1.23-bullseye AS builder

WORKDIR /app

# Build dependencies for sqlite (CGO)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY go.mod ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o scraper main.go

# Final runtime image with Chrome (small headless Chrome image)
FROM chromedp/headless-shell:latest

WORKDIR /app

# Copy compiled binary
COPY --from=builder /app/scraper /scraper

# Copy default config (can be overridden at runtime)
COPY config.json /app/config.json

ENV TZ=Etc/UTC

ENTRYPOINT ["/scraper"]
CMD ["--config=/app/config.json"]


