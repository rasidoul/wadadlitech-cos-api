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

# Fake HighLevel credentials so tests never depend on (or use) real
# tokens from a developer's local .env file. python-dotenv's load_dotenv()
# does not override variables already present in the environment, so
# these take precedence once set here.
os.environ.setdefault("HIGHLEVEL_WADADLITECH_TOKEN", "test-wadadlitech-token")
os.environ.setdefault("HIGHLEVEL_WADADLITECH_LOCATION_ID", "test-wadadlitech-location")
os.environ.setdefault("HIGHLEVEL_PARADIGM_TOKEN", "test-paradigm-token")
os.environ.setdefault("HIGHLEVEL_PARADIGM_LOCATION_ID", "test-paradigm-location")
os.environ.setdefault("HIGHLEVEL_JERMAINGORDON_TOKEN", "test-jermaingordon-token")
os.environ.setdefault("HIGHLEVEL_JERMAINGORDON_LOCATION_ID", "test-jermaingordon-location")

# Fake TradeHub credentials so tests never depend on a real deployed
# TradeHub instance or a developer's local .env file.
os.environ.setdefault("TRADEHUB_BASE_URL", "https://tradehub.test")
os.environ.setdefault("TRADEHUB_COS_API_KEY", "test-tradehub-cos-api-key")
os.environ.setdefault("TRADEHUB_TIMEOUT_SECONDS", "15")
os.environ.setdefault("COS_TRADEHUB_LIVE_EXECUTION_ENABLED", "false")


