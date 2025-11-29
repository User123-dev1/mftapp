"""
Transfer Rule Examples
Shows how to use all the new schedule types and action types
"""

from file_monitor import TransferRule, ScheduleType, TriggerType, ActionType

# ============================================================================
# EXAMPLE 1: EVENT-DRIVEN with COPY (Default - Keep Source)
# ============================================================================
# Monitors folder, auto-copies new files, keeps original

rule_event_copy = TransferRule(
    rule_id="event_copy_001",
    name="Event-Driven Copy",
    source_path="//192.168.252.16/C$/Users/bbaid/Documents/incoming",
    destination_path="C$/backup/documents",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    source_pattern="*.docx",          # Only Word documents
    schedule_type=ScheduleType.EVENT_DRIVEN,   # File events trigger transfer
    trigger_type=TriggerType.FILE_CREATED,     # When new file appears
    action_type=ActionType.COPY,               # Copy file (keep original)
    file_age_seconds=5                         # Wait 5s for file stability
)

print("Example 1: Event-Driven Copy")
print(f"  When: {rule_event_copy.trigger_type.value}")
print(f"  Action: {rule_event_copy.action_type.value}")
print(f"  Result: New .docx files auto-copy to backup, originals kept\n")


# ============================================================================
# EXAMPLE 2: EVENT-DRIVEN with MOVE (Delete Immediately)
# ============================================================================
# Monitors folder, auto-moves files, deletes original immediately

rule_event_move = TransferRule(
    rule_id="event_move_001",
    name="Event-Driven Move",
    source_path="//192.168.252.16/C$/Users/bbaid/Desktop/processed",
    destination_path="C$/archive/processed",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    source_pattern="*.pdf",
    schedule_type=ScheduleType.EVENT_DRIVEN,
    trigger_type=TriggerType.FILE_CREATED,
    action_type=ActionType.MOVE,               # Move file (delete source)
    file_age_seconds=10
)

print("Example 2: Event-Driven Move")
print(f"  When: {rule_event_move.trigger_type.value}")
print(f"  Action: {rule_event_move.action_type.value}")
print(f"  Result: New .pdf files moved to archive, originals deleted immediately\n")


# ============================================================================
# EXAMPLE 3: EVENT-DRIVEN with MOVE_WITH_DELAY
# ============================================================================
# Monitors folder, transfers files, deletes original after delay

rule_event_move_delay = TransferRule(
    rule_id="event_move_delay_001",
    name="Event-Driven Move with Delay",
    source_path="//192.168.252.16/C$/temp/uploads",
    destination_path="C$/secure/uploads",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    source_pattern="*.*",
    schedule_type=ScheduleType.EVENT_DRIVEN,
    trigger_type=TriggerType.FILE_CREATED,
    action_type=ActionType.MOVE_WITH_DELAY,    # Move with delayed deletion
    delete_delay_seconds=300,                  # Delete after 5 minutes
    file_age_seconds=5
)

print("Example 3: Event-Driven Move with Delay")
print(f"  When: {rule_event_move_delay.trigger_type.value}")
print(f"  Action: {rule_event_move_delay.action_type.value}")
print(f"  Delay: {rule_event_move_delay.delete_delay_seconds}s")
print(f"  Result: Files transferred, originals deleted after 5 minutes\n")


# ============================================================================
# EXAMPLE 4: ONCE (Execute One Time)
# ============================================================================
# Execute transfer once immediately, then stop

rule_once = TransferRule(
    rule_id="once_001",
    name="One-Time Transfer",
    source_path="//192.168.252.16/C$/database/backup.sql",
    destination_path="C$/archives/database/backup.sql",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    schedule_type=ScheduleType.ONCE,           # Execute once
    action_type=ActionType.COPY
)

print("Example 4: One-Time Transfer")
print(f"  Schedule: {rule_once.schedule_type.value}")
print(f"  Action: {rule_once.action_type.value}")
print(f"  Result: Transfer executes once immediately, then stops\n")


# ============================================================================
# EXAMPLE 5: RECURRING (Every X Minutes)
# ============================================================================
# Transfer files every X minutes

rule_recurring = TransferRule(
    rule_id="recurring_001",
    name="Hourly Backup",
    source_path="//192.168.252.16/C$/logs/application.log",
    destination_path="C$/backups/logs/application.log",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    schedule_type=ScheduleType.RECURRING,      # Recurring schedule
    schedule_interval_minutes=60,              # Every 60 minutes (hourly)
    action_type=ActionType.COPY
)

print("Example 5: Recurring Transfer")
print(f"  Schedule: {rule_recurring.schedule_type.value}")
print(f"  Interval: {rule_recurring.schedule_interval_minutes} minutes")
print(f"  Action: {rule_recurring.action_type.value}")
print(f"  Result: Transfers every hour automatically\n")


# ============================================================================
# EXAMPLE 6: CRON (Advanced Scheduling)
# ============================================================================
# Transfer on specific schedule (cron expression)

rule_cron = TransferRule(
    rule_id="cron_001",
    name="Daily Midnight Backup",
    source_path="//192.168.252.16/C$/data/reports",
    destination_path="C$/archive/daily",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    schedule_type=ScheduleType.CRON,           # Cron-based schedule
    schedule_cron="0 0 * * *",                 # Every day at midnight
    action_type=ActionType.MOVE
)

print("Example 6: Cron-Based Transfer")
print(f"  Schedule: {rule_cron.schedule_type.value}")
print(f"  Cron: {rule_cron.schedule_cron}")
print(f"  Action: {rule_cron.action_type.value}")
print(f"  Result: Transfers every day at midnight\n")


# ============================================================================
# EXAMPLE 7: ON_DEMAND (Manual Trigger)
# ============================================================================
# Transfer only when manually triggered via API/UI

rule_on_demand = TransferRule(
    rule_id="on_demand_001",
    name="Manual Transfer",
    source_path="//192.168.252.16/C$/exports/data.csv",
    destination_path="C$/imports/data.csv",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    schedule_type=ScheduleType.ON_DEMAND,      # Manual trigger only
    action_type=ActionType.COPY
)

print("Example 7: On-Demand Transfer")
print(f"  Schedule: {rule_on_demand.schedule_type.value}")
print(f"  Action: {rule_on_demand.action_type.value}")
print(f"  Result: Only executes when manually triggered\n")


# ============================================================================
# EXAMPLE 8: Multiple Triggers (Same Source, Different Destinations)
# ============================================================================
# Distribute one file to multiple servers

rule_multi_1 = TransferRule(
    rule_id="multi_001",
    name="Distribute to Server 1",
    source_path="//192.168.252.16/C$/exports/report.pdf",
    destination_path="C$/reports/report.pdf",
    protocol="unc",
    host="10.10.100.2",  # Server 1
    port=445,
    enabled=True,
    schedule_type=ScheduleType.EVENT_DRIVEN,
    trigger_type=TriggerType.FILE_CREATED,
    action_type=ActionType.COPY
)

rule_multi_2 = TransferRule(
    rule_id="multi_002",
    name="Distribute to Server 2",
    source_path="//192.168.252.16/C$/exports/report.pdf",  # Same source!
    destination_path="C$/reports/report.pdf",
    protocol="unc",
    host="10.10.100.4",  # Server 2
    port=445,
    enabled=True,
    schedule_type=ScheduleType.EVENT_DRIVEN,
    trigger_type=TriggerType.FILE_CREATED,
    action_type=ActionType.COPY
)

print("Example 8: Multi-Server Distribution")
print(f"  Rule 1 → {rule_multi_1.host}")
print(f"  Rule 2 → {rule_multi_2.host}")
print(f"  Result: One file appears, automatically copies to both servers\n")


# ============================================================================
# EXAMPLE 9: File Size Filtering
# ============================================================================
# Only transfer files within size range

rule_size_filter = TransferRule(
    rule_id="size_filter_001",
    name="Medium Files Only",
    source_path="//192.168.252.16/C$/uploads",
    destination_path="C$/processed",
    protocol="unc",
    host="10.10.100.4",
    port=445,
    enabled=True,
    source_pattern="*.jpg",
    schedule_type=ScheduleType.EVENT_DRIVEN,
    trigger_type=TriggerType.FILE_CREATED,
    action_type=ActionType.COPY,
    min_file_size=102400,      # At least 100 KB
    max_file_size=10485760     # Max 10 MB
)

print("Example 9: File Size Filtering")
print(f"  Min size: {rule_size_filter.min_file_size:,} bytes (100 KB)")
print(f"  Max size: {rule_size_filter.max_file_size:,} bytes (10 MB)")
print(f"  Result: Only JPGs between 100KB and 10MB transfer\n")


# ============================================================================
# CRON EXPRESSION EXAMPLES
# ============================================================================
print("="*80)
print("CRON EXPRESSION EXAMPLES")
print("="*80)
print()
print("Format: minute hour day month day-of-week")
print()
print("  '0 0 * * *'      - Every day at midnight")
print("  '0 */4 * * *'    - Every 4 hours")
print("  '0 9 * * 1-5'    - Weekdays at 9 AM")
print("  '30 2 * * 0'     - Sundays at 2:30 AM")
print("  '0 0 1 * *'      - First day of every month at midnight")
print("  '*/15 * * * *'   - Every 15 minutes")
print("  '0 12 * * *'     - Every day at noon")
print()


# ============================================================================
# SUMMARY OF OPTIONS
# ============================================================================
print("="*80)
print("SUMMARY OF ALL OPTIONS")
print("="*80)
print()
print("SCHEDULE TYPES:")
print("  - ONCE: Execute once immediately")
print("  - RECURRING: Repeat every X minutes")
print("  - CRON: Advanced scheduling with cron expressions")
print("  - ON_DEMAND: Manual trigger only")
print("  - EVENT_DRIVEN: Triggered by file events (create/modify/move)")
print()
print("ACTION TYPES:")
print("  - COPY: Copy file, keep original")
print("  - MOVE: Move file, delete original immediately")
print("  - MOVE_WITH_DELAY: Move file, delete original after delay")
print()
print("TRIGGER TYPES (for EVENT_DRIVEN):")
print("  - FILE_CREATED: When new file appears")
print("  - FILE_MODIFIED: When file is modified")
print("  - FILE_MOVED: When file is moved/renamed")
print()
print("="*80)