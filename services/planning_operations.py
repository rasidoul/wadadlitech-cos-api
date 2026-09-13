"""Daily and weekly planning and review endpoints.

Provides deterministic planning logic without LLM calls:
- Daily planning: combine emails, tasks, calendar; suggest 3 outcomes
- Weekly review: planned vs actual, overdue, waiting-for, active projects

Uses existing task, Gmail, and Calendar data to synthesize actionable plans.
"""

import json
from typing import Any, Dict, List, Optional
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"


class PlanningError(Exception):
    pass


def _parse_iso_date(date_str: Optional[str]) -> Optional[date]:
    """Parse ISO date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date()
    except (ValueError, TypeError):
        return None


async def get_daily_plan(
    max_outcomes: int = 3,
    buffer_percentage: int = 30,
    timezone_name: str = DEFAULT_TIMEZONE,
    # These would be fetched from tasks, Gmail, Calendar in real impl
    tasks: Optional[List[Dict[str, Any]]] = None,
    calendar_events: Optional[List[Dict[str, Any]]] = None,
    email_count: int = 0,
) -> Dict[str, Any]:
    """Generate daily plan for today.
    
    Algorithm:
    1. Collect relevant tasks (due today, high priority, open status)
    2. Identify calendar commitments and free blocks
    3. Calculate available time (excluding buffer)
    4. Sort tasks by priority/deadline
    5. Fit tasks into available slots
    6. Flag blocked tasks, impossible deadlines
    7. Return top N outcomes with time estimates and reasons
    
    Args:
        max_outcomes: Number of main outcomes to return (default 3)
        buffer_percentage: Percentage of time to reserve as buffer (default 30)
        timezone_name: Timezone for interpretation
        tasks: List of task dicts (from tasks service)
        calendar_events: List of event dicts (from calendar service)
        email_count: Number of pending emails (from Gmail)
    
    Returns:
        Dict with plan, outcomes, conflicts, warnings, recommendations
    """
    
    if tasks is None:
        tasks = []
    if calendar_events is None:
        calendar_events = []
    
    today = date.today()
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    
    # --- Step 1: Identify relevant tasks ---
    
    relevant_tasks = []
    
    for task in tasks:
        if task.get("status") == "COMPLETE":
            continue
        
        task_due = _parse_iso_date(task.get("due_date"))
        
        # Include if due today or overdue
        if task_due and task_due <= today:
            relevant_tasks.append({
                **task,
                "urgency": "today" if task_due == today else "overdue",
            })
        elif task.get("priority") in ["CRITICAL", "HIGH"]:
            # Include high-priority tasks even without due date
            relevant_tasks.append({
                **task,
                "urgency": "high_priority",
            })
    
    # --- Step 2: Calculate available time ---
    
    # Assume a standard work day: 9am-5pm (8 hours). Using the full planning day
    # keeps daily recommendation logic deterministic and ensures due tasks can still
    # fit when the current clock is later in the day.
    work_start = datetime.combine(today, datetime.min.time(), tzinfo=tz).replace(hour=9, minute=0, second=0, microsecond=0)
    work_end = work_start.replace(hour=17, minute=0, second=0, microsecond=0)

    # If already past 5pm, begin the planning window tomorrow instead of zeroing out.
    if now > work_end:
        work_start = (work_start + timedelta(days=1))
        work_end = work_start.replace(hour=17, minute=0, second=0, microsecond=0)
    
    total_minutes = int((work_end - work_start).total_seconds() / 60)
    buffer_minutes = int(total_minutes * buffer_percentage / 100)
    available_minutes = total_minutes - buffer_minutes
    
    # Account for calendar events
    busy_minutes = 0
    conflicts = []
    
    for event in calendar_events:
        # Skip all-day, free-marked, or cancelled events
        if (
            event.get("is_all_day")
            or event.get("transparency") == "transparent"
            or event.get("status") == "cancelled"
        ):
            continue
        
        # Calculate duration
        try:
            start = datetime.fromisoformat(
                event.get("start_time", "").replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(
                event.get("end_time", "").replace("Z", "+00:00")
            )
            duration = int((end - start).total_seconds() / 60)
            busy_minutes += duration
            
            conflicts.append({
                "event": event.get("summary"),
                "duration_minutes": duration,
                "start": event.get("start_time"),
            })
        except (ValueError, TypeError):
            pass
    
    available_minutes -= busy_minutes
    
    # --- Step 3: Sort tasks by priority ---
    
    priority_order = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3, "BACKLOG": 4}
    
    relevant_tasks.sort(
        key=lambda t: (
            priority_order.get(t.get("priority"), 5),
            t.get("urgency") == "today" and 0 or 1,  # due today first
            t.get("due_date") or "9999-12-31",  # soonest deadline
        )
    )
    
    # --- Step 4: Fit tasks into available time ---
    
    outcomes = []
    scheduled_minutes = 0
    blocked_tasks = []
    warnings = []
    
    for task in relevant_tasks:
        task_id = task.get("id")
        title = task.get("title")
        estimated = task.get("estimated_minutes", 30)
        blocker = task.get("blocker")
        
        if blocker:
            blocked_tasks.append({
                "task_id": task_id,
                "title": title,
                "blocker": blocker,
                "action": f"Resolve: {blocker}",
            })
            continue
        
        if scheduled_minutes + estimated <= available_minutes:
            outcomes.append({
                "task_id": task_id,
                "title": title,
                "priority": task.get("priority"),
                "estimated_minutes": estimated,
                "reason": f"Fits in available time ({available_minutes - scheduled_minutes}min remaining)",
            })
            scheduled_minutes += estimated
            
            if len(outcomes) >= max_outcomes:
                break
        else:
            warnings.append({
                "task_id": task_id,
                "title": title,
                "reason": f"Not enough time ({estimated}min needed, {available_minutes - scheduled_minutes}min available)",
            })
    
    # --- Step 5: Identify issues ---
    
    for task in relevant_tasks[len(outcomes):]:
        if task.get("postponement_count", 0) >= 2:
            warnings.append({
                "task_id": task.get("id"),
                "title": task.get("title"),
                "reason": "Postponed 2+ times; needs reassessment",
            })
    
    return {
        "date": today.isoformat(),
        "timezone": timezone_name,
        "work_hours": {
            "start": work_start.isoformat(),
            "end": work_end.isoformat(),
            "total_minutes": total_minutes,
            "buffer_minutes": buffer_minutes,
            "available_after_buffer_minutes": available_minutes,
        },
        "outcomes": outcomes[:max_outcomes],
        "blocked_tasks": blocked_tasks,
        "warnings": warnings[:max_outcomes],
        "calendar_conflicts": conflicts,
        "pending_emails": email_count,
        "email_note": "Review pending emails for requests/deadlines",
        "summary": f"{len(outcomes)} main outcomes scheduled, {len(blocked_tasks)} blocked, {len(warnings)} warnings",
    }


async def get_weekly_review(
    tasks: Optional[List[Dict[str, Any]]] = None,
    completed_count: int = 0,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> Dict[str, Any]:
    """Generate weekly review report.
    
    Reports:
    - Planned vs completed outcomes
    - Overdue and repeatedly postponed tasks
    - Waiting-for follow-ups
    - Active projects missing next action
    - Upcoming deadlines (next 7 days)
    - Capacity and time allocation
    - 1-2 evidence-based adjustments
    
    Args:
        tasks: List of task dicts
        completed_count: Number of tasks completed this week
        timezone_name: Timezone
    
    Returns:
        Dict with review report
    """
    
    if tasks is None:
        tasks = []
    
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = today
    next_week_end = today + timedelta(days=7)
    
    # --- Categorize tasks ---
    
    overdue = []
    due_this_week = []
    due_next_week = []
    waiting_for = []
    active_projects = {}
    no_next_action_projects = []
    
    for task in tasks:
        if task.get("status") == "COMPLETE":
            continue
        
        task_due = _parse_iso_date(task.get("due_date"))
        project = task.get("project", "No project")
        blocker = task.get("blocker")
        waiting_date = _parse_iso_date(task.get("waiting_for_date"))
        next_action = task.get("next_action")
        
        # Track overdue
        if task_due and task_due < today:
            overdue.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "due": task_due.isoformat(),
                "days_overdue": (today - task_due).days,
                "priority": task.get("priority"),
            })
        
        # Track this week
        if task_due and week_start <= task_due <= week_end:
            due_this_week.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "due": task_due.isoformat(),
                "priority": task.get("priority"),
            })
        
        # Track next week
        if task_due and week_end < task_due <= next_week_end:
            due_next_week.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "due": task_due.isoformat(),
                "priority": task.get("priority"),
            })
        
        # Track waiting-for
        if waiting_date:
            waiting_for.append({
                "id": task.get("id"),
                "title": task.get("title"),
                "waiting_until": waiting_date.isoformat(),
                "blocker": blocker,
            })
        
        # Track projects with next action
        if project not in active_projects:
            active_projects[project] = {
                "tasks": 0,
                "has_next_action": False,
            }
        
        active_projects[project]["tasks"] += 1
        if next_action:
            active_projects[project]["has_next_action"] = True
    
    # Find projects without next action
    for project, info in active_projects.items():
        if not info["has_next_action"] and info["tasks"] > 0:
            no_next_action_projects.append({
                "project": project,
                "task_count": info["tasks"],
                "action": "Identify and assign next action",
            })
    
    # --- Identify adjustments ---
    
    adjustments = []
    
    # Adjustment 1: If many overdue, suggest triage
    if len(overdue) > 3:
        adjustments.append({
            "type": "triage",
            "description": f"Large overdue backlog ({len(overdue)} tasks)",
            "action": "Review and reschedule or close overdue tasks",
        })
    
    # Adjustment 2: If no projects with next action, suggest planning
    if len(no_next_action_projects) > 0:
        adjustments.append({
            "type": "planning",
            "description": f"{len(no_next_action_projects)} active projects lack next action",
            "action": "Assign clear next action to keep projects moving",
        })
    
    return {
        "period": {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "today": today.isoformat(),
        },
        "outcomes": {
            "completed_this_week": completed_count,
            "overdue": len(overdue),
            "due_this_week": len(due_this_week),
            "due_next_week": len(due_next_week),
        },
        "overdue_tasks": overdue,
        "due_this_week": due_this_week,
        "due_next_week": due_next_week,
        "waiting_for": waiting_for,
        "active_projects_without_next_action": no_next_action_projects,
        "total_active_projects": len(active_projects),
        "adjustments": adjustments,
        "timezone": timezone_name,
    }
