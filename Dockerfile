# The corpus is baked into the image and never written to at runtime. There is
# no state here at all: no metrics store, no user data, no local/ overlay. A
# redeploy is the only way its answers change, which is the correct coupling
# for a project whose output is supposed to be reproducible from its YAML.
FROM python:3.14-slim

WORKDIR /app

# Editable install on purpose. Without it the package lands in site-packages,
# where there is no data/ directory and DEFAULT_DB resolves somewhere useless
# -- the same reason the local venv is editable.
COPY pyproject.toml README.md LICENSE LICENSE-DOCS ./
COPY src/ ./src/
COPY data/ ./data/
RUN pip install --no-cache-dir -e ".[gui]"

# Build at image time so a container start cannot fail on a corpus error, and
# so a broken corpus fails the build rather than the deploy.
RUN python -m xycalc.build && python -m xycalc.audit

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "xycalc.api:app", "--host", "0.0.0.0", "--port", "8000"]
