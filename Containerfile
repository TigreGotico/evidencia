FROM python:3.12-slim

# cpu wheels by default; build with --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 for CUDA
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV UV_SYSTEM_PYTHON=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/models

RUN pip install --no-cache-dir "uv==0.12.9" \
 && uv pip install --no-cache --index-url "$TORCH_INDEX" torch

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY evidencia ./evidencia
# the container reaches its chat model over HTTP, through a built-in generator
RUN uv pip install --no-cache --prerelease=allow ".[local,hybrid]"

RUN useradd --create-home --uid 1000 evidencia && mkdir -p /models /collections \
 && chown evidencia /models /collections
USER evidencia
VOLUME /models /collections
EXPOSE 8080
CMD ["evidencia", "serve", "--host", "0.0.0.0", "--port", "8080"]
