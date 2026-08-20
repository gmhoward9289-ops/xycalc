# One image for the worker and the driver; they differ only in what they run.
#
# Versions are pinned, not floored with >=. An unpinned floor against a
# floating base image tag means a build today and a build next month can run
# different Celery versions with no record of which -- and no coefficient
# derived from this harness's JSON output can have an honest applies_to
# without knowing what actually ran. drive.py also records the resolved
# versions into its JSON output at runtime, so a rebuild that changes these
# pins is still traceable from the data itself.
FROM python:3.12-slim
RUN pip install --no-cache-dir "celery[redis]==5.4.0" "pymongo==4.9.1" "redis==5.0.8"
WORKDIR /app
COPY tasks.py drive.py evict_probe.py ./
