# ============================================================================
# pda-grader sandbox image — the ONLY place untrusted student notebooks execute.
# Built once per CI run (cached by content hash).  The container is always run
# locked down by grader/runner.py: --network none, non-root, read-only rootfs,
# CPU/RAM/PID caps, a hard wall-clock timeout, and ephemeral (--rm).
# See STRICT-INSTRUCTIONS.md §1.4.
# ============================================================================
FROM python:3.11-slim

# Non-root runner (student code never runs as root).
RUN useradd -m -u 1000 runner

# The data-science stack students' notebooks expect, plus the execution tools.
# Pinned for reproducible grading across runs.
RUN pip install --no-cache-dir \
      numpy==2.1.3 \
      pandas==2.2.3 \
      scikit-learn==1.5.2 \
      scipy==1.14.1 \
      matplotlib==3.9.2 \
      papermill==2.6.0 \
      ipykernel==6.29.5 \
      nbformat==5.10.4 \
      nbclient==0.10.0 \
 && python -m ipykernel install --name python3

USER runner
WORKDIR /work

# Sanity check target (used by the G1 DoD):
#   docker run --rm --network none pda-sandbox python -c "import pandas,sklearn;print('ok')"
