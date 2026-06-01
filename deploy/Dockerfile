# Standalone service image (shared by the API and worker processes).
#
# Everything is in THIS repo — the pallet_packer core is vendored under core/ and
# built here; there is no external dependency and no host mount. The builder stage
# compiles the core's 6 Cython extensions into a wheel; the slim runtime stage
# installs that wheel + the service deps. `docker compose up --build` just works.

# ---------- builder: compile the vendored core into a wheel ----------
FROM python:3.13-slim-bookworm AS builder
# build-essential = gcc/g++ (the .pyx are compiled with -O3/-ffast-math, and the
# two batch decoders with -fopenmp). Cython + numpy come via the core's
# pyproject build-system (PEP 517 isolated build).
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY core/ /build/core/
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels /build/core/

# ---------- runtime: slim image, no toolchain ----------
FROM python:3.13-slim-bookworm
# libgomp1 = the OpenMP runtime the core's prange-parallel .so link against
# (-fopenmp). Runtime lib only — no compiler in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
# Service deps + the vendored core wheel (which pulls numpy/numba/ortools).
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.29" "arq>=0.26" "redis>=5,<6" \
        "pydantic>=2.6" \
        /wheels/pallet_packer*.whl \
    && rm -rf /wheels

WORKDIR /app
COPY src/ /app/src/

# pallet_packer is an installed package (site-packages); only the service package
# needs to be on PYTHONPATH.
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2

EXPOSE 8000
CMD ["uvicorn", "pallet_api.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
