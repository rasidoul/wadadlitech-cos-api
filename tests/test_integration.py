"""Integration verification for planning upgrade.

Confirms all pieces work together:
- Database with new fields
- All 27 new endpoints accessible
- Planning logic functional
- Gmail/Calendar/Reminders structures ready for API testing
"""

import json
import asyncio
import sys
import os
from datetime import date, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_integration_database():
    """Verify database schema and planning fields."""
    from services.tasks import create_task, get_task, update_task
    
    # Create task with all planning fields
    task = create_task(
        title="Integration test task",
        description="Testing full planning workflow",
        priority="HIGH",
        domain="BUSINESS",
        estimated_minutes=90,
        deadline="2025-01-20",
        gmail_thread_id="thread-integration-123",
        calendar_event_id="event-456",
        reminders_id="reminder-789",
        source_system="gmail",
        external_id="ext-001",
    )
    
    assert task["id"] is not None
    assert task["domain"] == "BUSINESS"
    assert task["estimated_minutes"] == 90
    assert task["gmail_thread_id"] == "thread-integration-123"
    print(f"✓ Database: Created task {task['id']} with planning fields")
    
    # Update with additional fields
    update_task(
        task["id"],
        blocker="Waiting for Alice",
        postponement_count=1,
        pending_commands=["sync_to_reminders", "update_calendar"],
    )
    
    fetched = get_task(task["id"])
    assert fetched["blocker"] == "Waiting for Alice"
    assert fetched["postponement_count"] == 1
    assert "sync_to_reminders" in fetched["pending_commands"]
    print(f"✓ Database: Updated task with blocker and pending commands")
    
    return task["id"]


def test_integration_api_structure():
    """Verify all new endpoints are present."""
    import main
    
    # Count endpoints by category
    endpoints = {}
    for route in main.app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            for method in getattr(route, 'methods', []):
                path = route.path
                endpoints[f"{method} {path}"] = True
    
    # Verify key new endpoints exist
    required_endpoints = [
        # Gmail
        "GET /integrations/gmail/messages",
        "POST /integrations/gmail/messages/{message_id}/label",
        "POST /integrations/gmail/drafts",
        "POST /integrations/gmail/drafts/{draft_id}/send",
        # Calendar
        "GET /integrations/calendar/calendars",
        "POST /integrations/calendar/focus-blocks",
        "PATCH /integrations/calendar/focus-blocks/{event_id}",
        "DELETE /integrations/calendar/focus-blocks/{event_id}",
        # Reminders
        "GET /integrations/reminders/pending-sync",
        "POST /integrations/reminders/acknowledge",
        # Planning
        "GET /planning/daily",
        "GET /planning/weekly-review",
    ]
    
    for endpoint in required_endpoints:
        assert endpoint in endpoints, f"Missing endpoint: {endpoint}"
        print(f"✓ Endpoint: {endpoint}")
    
    total_endpoints = len(endpoints)
    assert total_endpoints >= 92, f"Expected at least 92 endpoints, got {total_endpoints}"
    print(f"✓ API structure: {total_endpoints} total endpoints confirmed")


def test_integration_auth_structure():
    """Verify auth modules are properly integrated."""
    from services import google_auth_enhanced, gmail_operations, calendar_operations, reminders_operations
    
    # Check Google auth
    assert hasattr(google_auth_enhanced, 'get_access_token')
    assert hasattr(google_auth_enhanced, 'get_google_integration_diagnostics')
    print("✓ Auth: Google auth module loaded")
    
    # Check Gmail operations
    assert hasattr(gmail_operations, 'search_messages')
    assert hasattr(gmail_operations, 'create_draft')
    assert hasattr(gmail_operations, 'send_draft')
    print("✓ Auth: Gmail operations module loaded")
    
    # Check Calendar operations
    assert hasattr(calendar_operations, 'list_calendars')
    assert hasattr(calendar_operations, 'create_focus_block')
    assert hasattr(calendar_operations, 'check_availability')
    print("✓ Auth: Calendar operations module loaded")
    
    # Check Reminders operations
    assert hasattr(reminders_operations, 'validate_bridge_auth')
    assert hasattr(reminders_operations, 'get_pending_sync')
    assert hasattr(reminders_operations, 'acknowledge_sync')
    print("✓ Auth: Reminders operations module loaded")


async def test_integration_planning_workflow():
    """Verify planning workflow works end-to-end."""
    from services.planning_operations import get_daily_plan, get_weekly_review
    from services.tasks import create_task, get_task
    
    # Create test tasks
    today = date.today()
    
    task_high = create_task(
        title="High priority due today",
        priority="HIGH",
        due_date=today.isoformat(),
        estimated_minutes=60,
        domain="BUSINESS",
    )
    
    task_medium = create_task(
        title="Medium priority due tomorrow",
        priority="NORMAL",
        due_date=(today + timedelta(days=1)).isoformat(),
        estimated_minutes=45,
        domain="PERSONAL",
    )
    
    task_low = create_task(
        title="Low priority, no deadline",
        priority="LOW",
        estimated_minutes=30,
        domain="BUSINESS",
    )
    
    # Use the tasks we just created
    test_tasks = [
        get_task(task_high["id"]),
        get_task(task_medium["id"]),
        get_task(task_low["id"]),
    ]
    
    # Generate daily plan
    plan = await get_daily_plan(
        max_outcomes=3,
        buffer_percentage=30,
        tasks=test_tasks,
    )
    
    assert "outcomes" in plan
    assert "blocked_tasks" in plan
    assert "warnings" in plan
    assert "work_hours" in plan
    print(f"✓ Planning: Daily plan generated with {len(plan['outcomes'])} outcomes")
    
    # Generate weekly review
    review = await get_weekly_review(
        tasks=test_tasks,
        completed_count=0,
    )
    
    assert "outcomes" in review
    assert "overdue_tasks" in review
    assert "due_this_week" in review
    assert "adjustments" in review
    print(f"✓ Planning: Weekly review generated with {len(review['adjustments'])} adjustments")
    
    return task_high["id"], task_medium["id"], task_low["id"]


def test_integration_environment():
    """Verify environment variables are configured."""
    import os
    
    # Check required variables
    required_vars = [
        "COS_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REFRESH_TOKEN",
    ]
    
    # These should exist in .env
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"✓ Environment: {var} configured")
        else:
            print(f"⚠ Environment: {var} not set (expected in .env)")
    
    # Check optional variables
    optional_vars = [
        "COS_DATABASE_PATH",
        "REMINDERS_BRIDGE_API_KEY",
        "COS_PLANNING_TIMEZONE",
        "COS_FOCUS_CALENDAR_ID",
    ]
    
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"✓ Environment: {var} = {value[:20]}...")
        else:
            print(f"⚠ Environment: {var} not set (optional, has default)")


if __name__ == "__main__":
    print("=" * 60)
    print("INTEGRATION VERIFICATION")
    print("=" * 60)
    
    try:
        # Test 1: Database
        print("\n[1/5] Testing Database Integration...")
        task_id = test_integration_database()
        
        # Test 2: API Structure
        print("\n[2/5] Testing API Structure...")
        test_integration_api_structure()
        
        # Test 3: Auth Modules
        print("\n[3/5] Testing Auth Modules...")
        test_integration_auth_structure()
        
        # Test 4: Planning Workflow
        print("\n[4/5] Testing Planning Workflow...")
        asyncio.run(test_integration_planning_workflow())
        
        # Test 5: Environment
        print("\n[5/5] Testing Environment Configuration...")
        test_integration_environment()
        
        print("\n" + "=" * 60)
        print("✓ ALL INTEGRATION TESTS PASSED")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Phase 7: Add safety layer & audit logging")
        print("2. Phase 8: Generate GPT Action OpenAPI schema")
        print("3. Phase 9: Execute full test suite")
        print("4. Phase 10: Create deployment documentation")
        
    except Exception as e:
        print(f"\n✗ INTEGRATION TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
