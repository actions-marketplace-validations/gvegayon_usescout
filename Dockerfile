# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install /usr/local
LABEL org.opencontainers.image.source="https://github.com/gvegayon/whouses" \
      org.opencontainers.image.description="Track who uses your research software" \
      org.opencontainers.image.licenses="MIT"
ENTRYPOINT ["whouses"]
CMD ["action"]
