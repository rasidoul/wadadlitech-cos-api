import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Configure environment before "main" (and its services) are imported by
# any test module, so tests never touch production credentials or data.
os.environ.setdefault("COS_API_KEY", "test-cos-api-key")

_tmp_dir = tempfile.mkdtemp(prefix="wadadlitech-cos-api-tests-")
os.environ["AGENT_BRIEFS_DB_PATH"] = os.path.join(
    _tmp_dir, "agent_briefs_test.db"
)

# Ensure the webhook starts "not configured" unless a test opts in.
os.environ.pop("MAYA_WD_WEBHOOK_URL", None)
