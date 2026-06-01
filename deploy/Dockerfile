# Service image (shared by the API and worker processes).
#
# DEV: the pallet_packer core is mounted at /core (docker-compose volume) and put
#      on PYTHONPATH, reusing its already-built platform .so. See D10.
# PROD: drop the /core mount and instead bake the core into the image, e.g.:
#         RUN apt-get install -y build-essential gcc g++ git \
#          && pip install "pallet-packer @ git+https://github.com/anzozulia/pallet_stacker_research.git@<TAG>"
#       (this builds the Cython extensions in the image) and remove /core from
#       PYTHONPATH below.
FROM python:3.13-slim-bookworm

# Service deps + the core's RUNTIME deps (the mounted core needs these to import).
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.29" "arq>=0.26" "redis>=5,<6" \
        "pydantic>=2.6" \
        "numpy>=2.4,<2.5" "numba>=0.65,<0.66" "ortools>=9.10,<10.0"

# libgomp1 = the OpenMP runtime the core's prange-parallel .so link against
# (-fopenmp). Without it the batch Cython decoders fail to load and the solver
# falls back to a non-existent batch path. Runtime lib only, no toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/ /app/src/

# /app/src -> the service package; /core -> the mounted pallet_packer core (dev).
ENV PYTHONPATH=/app/src:/core \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2

EXPOSE 8000
CMD ["uvicorn", "pallet_api.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
