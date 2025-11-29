"""
Scheduler System for MFT Application
Supports automated, scheduled, and recurring file transfers
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from croniter import croniter
import uuid

from mft_application import MFTApplication, TransferConfig

logger = logging.getLogger(__name__)


class ScheduleType(Enum):
    """Types of schedules"""
    ONCE = "once"
    RECURRING = "recurring"
    CRON = "cron"
    ON_DEMAND = "on_demand"
    EVENT_DRIVEN = "event_driven"


class ScheduleStatus(Enum):
    """Schedule status"""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class ScheduledTransfer:
    """Scheduled transfer definition"""
    schedule_id: str
    name: str
    description: str
    schedule_type: ScheduleType
    source_path: str
    destination_path: str
    config: TransferConfig
    
    # Schedule settings
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    cron_expression: Optional[str] = None  # For CRON schedules
    interval_minutes: Optional[int] = None  # For RECURRING schedules
    
    # Execution tracking
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    last_execution: Optional[datetime] = None
    next_execution: Optional[datetime] = None
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    
    # Advanced settings
    retry_failed: bool = True
    max_retries: int = 3
    continue_on_error: bool = True
    notification_on_success: bool = False
    notification_on_failure: bool = True
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'schedule_id': self.schedule_id,
            'name': self.name,
            'description': self.description,
            'schedule_type': self.schedule_type.value,
            'source_path': self.source_path,
            'destination_path': self.destination_path,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'cron_expression': self.cron_expression,
            'interval_minutes': self.interval_minutes,
            'status': self.status.value,
            'last_execution': self.last_execution.isoformat() if self.last_execution else None,
            'next_execution': self.next_execution.isoformat() if self.next_execution else None,
            'execution_count': self.execution_count,
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'created_at': self.created_at.isoformat(),
            'created_by': self.created_by,
            'tags': self.tags
        }


@dataclass
class ExecutionResult:
    """Result of a scheduled execution"""
    schedule_id: str
    execution_time: datetime
    success: bool
    task_id: Optional[str] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None


class TransferScheduler:
    """Manages scheduled file transfers"""
    
    def __init__(self, mft_app: MFTApplication):
        self.mft_app = mft_app
        self.schedules: Dict[str, ScheduledTransfer] = {}
        self.execution_history: List[ExecutionResult] = []
        self.event_handlers: Dict[str, Callable] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        
    async def start(self):
        """Start the scheduler"""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Transfer scheduler started")
    
    async def stop(self):
        """Stop the scheduler"""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("Transfer scheduler stopped")
    
    def add_schedule(self, schedule: ScheduledTransfer) -> str:
        """Add a new schedule"""
        # Validate schedule
        self._validate_schedule(schedule)
        
        # Calculate next execution time
        schedule.next_execution = self._calculate_next_execution(schedule)
        
        # Add to schedules
        self.schedules[schedule.schedule_id] = schedule
        
        logger.info(f"Added schedule: {schedule.name} ({schedule.schedule_id})")
        
        return schedule.schedule_id
    
    def remove_schedule(self, schedule_id: str):
        """Remove a schedule"""
        if schedule_id in self.schedules:
            del self.schedules[schedule_id]
            logger.info(f"Removed schedule: {schedule_id}")
    
    def update_schedule(self, schedule_id: str, **kwargs):
        """Update a schedule"""
        if schedule_id not in self.schedules:
            raise ValueError(f"Schedule not found: {schedule_id}")
        
        schedule = self.schedules[schedule_id]
        
        for key, value in kwargs.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)
        
        # Recalculate next execution
        schedule.next_execution = self._calculate_next_execution(schedule)
        
        logger.info(f"Updated schedule: {schedule_id}")
    
    def pause_schedule(self, schedule_id: str):
        """Pause a schedule"""
        if schedule_id in self.schedules:
            self.schedules[schedule_id].status = ScheduleStatus.PAUSED
            logger.info(f"Paused schedule: {schedule_id}")
    
    def resume_schedule(self, schedule_id: str):
        """Resume a paused schedule"""
        if schedule_id in self.schedules:
            schedule = self.schedules[schedule_id]
            schedule.status = ScheduleStatus.ACTIVE
            schedule.next_execution = self._calculate_next_execution(schedule)
            logger.info(f"Resumed schedule: {schedule_id}")
    
    def trigger_schedule(self, schedule_id: str):
        """Manually trigger a schedule"""
        if schedule_id not in self.schedules:
            raise ValueError(f"Schedule not found: {schedule_id}")
        
        schedule = self.schedules[schedule_id]
        asyncio.create_task(self._execute_schedule(schedule))
    
    def get_schedule(self, schedule_id: str) -> Optional[ScheduledTransfer]:
        """Get a schedule by ID"""
        return self.schedules.get(schedule_id)
    
    def list_schedules(self, 
                      status: Optional[ScheduleStatus] = None,
                      tags: Optional[List[str]] = None) -> List[ScheduledTransfer]:
        """List schedules with optional filtering"""
        schedules = list(self.schedules.values())
        
        if status:
            schedules = [s for s in schedules if s.status == status]
        
        if tags:
            schedules = [s for s in schedules if any(tag in s.tags for tag in tags)]
        
        return schedules
    
    def get_execution_history(self, 
                            schedule_id: Optional[str] = None,
                            limit: int = 100) -> List[ExecutionResult]:
        """Get execution history"""
        history = self.execution_history
        
        if schedule_id:
            history = [h for h in history if h.schedule_id == schedule_id]
        
        return history[:limit]
    
    def register_event_handler(self, event: str, handler: Callable):
        """Register an event handler"""
        self.event_handlers[event] = handler
        logger.info(f"Registered event handler: {event}")
    
    async def _scheduler_loop(self):
        """Main scheduler loop"""
        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                await self._check_schedules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
    
    async def _check_schedules(self):
        """Check and execute due schedules"""
        now = datetime.utcnow()
        
        for schedule in list(self.schedules.values()):
            # Skip if not active
            if schedule.status != ScheduleStatus.ACTIVE:
                continue
            
            # Skip if no next execution time
            if not schedule.next_execution:
                continue
            
            # Check if end time has passed
            if schedule.end_time and now > schedule.end_time:
                schedule.status = ScheduleStatus.COMPLETED
                logger.info(f"Schedule completed (end time reached): {schedule.name}")
                continue
            
            # Check if execution is due
            if now >= schedule.next_execution:
                # Execute the schedule
                asyncio.create_task(self._execute_schedule(schedule))
    
    async def _execute_schedule(self, schedule: ScheduledTransfer):
        """Execute a scheduled transfer"""
        start_time = datetime.utcnow()
        
        logger.info(f"Executing schedule: {schedule.name} ({schedule.schedule_id})")
        
        try:
            # Execute the transfer
            task_id = await self.mft_app.transfer_file(
                source_path=schedule.source_path,
                destination_path=schedule.destination_path,
                config=schedule.config,
                metadata={
                    'schedule_id': schedule.schedule_id,
                    'schedule_name': schedule.name,
                    **schedule.metadata
                }
            )
            
            # Wait for completion (with timeout)
            timeout = schedule.config.timeout if schedule.config else 3600
            await self._wait_for_completion(task_id, timeout)
            
            # Check if successful
            transfer_status = self.mft_app.get_transfer_status(task_id)
            success = transfer_status and transfer_status['status'] == 'completed'
            
            # Update schedule
            schedule.last_execution = start_time
            schedule.execution_count += 1
            
            if success:
                schedule.success_count += 1
            else:
                schedule.failure_count += 1
            
            # Calculate next execution
            schedule.next_execution = self._calculate_next_execution(schedule)
            
            # Record execution result
            duration = (datetime.utcnow() - start_time).total_seconds()
            result = ExecutionResult(
                schedule_id=schedule.schedule_id,
                execution_time=start_time,
                success=success,
                task_id=task_id,
                error_message=transfer_status.get('error_message') if transfer_status else None,
                duration_seconds=duration
            )
            self.execution_history.append(result)
            
            # Trigger events
            await self._trigger_event('execution_completed', schedule, result)
            
            if success and schedule.notification_on_success:
                await self._trigger_event('execution_success', schedule, result)
            elif not success and schedule.notification_on_failure:
                await self._trigger_event('execution_failure', schedule, result)
            
            logger.info(f"Schedule execution {'succeeded' if success else 'failed'}: {schedule.name}")
            
        except Exception as e:
            logger.error(f"Error executing schedule {schedule.name}: {e}", exc_info=True)
            
            schedule.last_execution = start_time
            schedule.execution_count += 1
            schedule.failure_count += 1
            
            # Calculate next execution
            schedule.next_execution = self._calculate_next_execution(schedule)
            
            # Record failure
            duration = (datetime.utcnow() - start_time).total_seconds()
            result = ExecutionResult(
                schedule_id=schedule.schedule_id,
                execution_time=start_time,
                success=False,
                error_message=str(e),
                duration_seconds=duration
            )
            self.execution_history.append(result)
            
            # Trigger failure event
            if schedule.notification_on_failure:
                await self._trigger_event('execution_failure', schedule, result)
    
    async def _wait_for_completion(self, task_id: str, timeout: int):
        """Wait for a transfer to complete"""
        elapsed = 0
        check_interval = 5
        
        while elapsed < timeout:
            status = self.mft_app.get_transfer_status(task_id)
            
            if status and status['status'] in ['completed', 'failed', 'cancelled']:
                return
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        
        raise TimeoutError(f"Transfer {task_id} timed out after {timeout} seconds")
    
    def _calculate_next_execution(self, schedule: ScheduledTransfer) -> Optional[datetime]:
        """Calculate the next execution time for a schedule"""
        now = datetime.utcnow()
        
        # Check if schedule has ended
        if schedule.end_time and now > schedule.end_time:
            return None
        
        # ONCE schedule
        if schedule.schedule_type == ScheduleType.ONCE:
            if schedule.start_time and schedule.start_time > now:
                return schedule.start_time
            elif not schedule.last_execution:
                return now
            else:
                return None  # Already executed
        
        # RECURRING schedule
        elif schedule.schedule_type == ScheduleType.RECURRING:
            if not schedule.interval_minutes:
                raise ValueError("Interval required for recurring schedule")
            
            if not schedule.last_execution:
                # First execution
                return schedule.start_time if schedule.start_time else now
            else:
                # Next execution based on interval
                next_time = schedule.last_execution + timedelta(minutes=schedule.interval_minutes)
                return next_time if next_time > now else now
        
        # CRON schedule
        elif schedule.schedule_type == ScheduleType.CRON:
            if not schedule.cron_expression:
                raise ValueError("Cron expression required for cron schedule")
            
            try:
                base_time = schedule.last_execution if schedule.last_execution else now
                cron = croniter(schedule.cron_expression, base_time)
                next_time = cron.get_next(datetime)
                return next_time
            except Exception as e:
                logger.error(f"Invalid cron expression: {schedule.cron_expression} - {e}")
                return None
        
        # ON_DEMAND schedule
        elif schedule.schedule_type == ScheduleType.ON_DEMAND:
            return None  # Manually triggered
        
        return None
    
    def _validate_schedule(self, schedule: ScheduledTransfer):
        """Validate a schedule"""
        if schedule.schedule_type == ScheduleType.RECURRING:
            if not schedule.interval_minutes or schedule.interval_minutes <= 0:
                raise ValueError("Recurring schedule requires positive interval_minutes")
        
        elif schedule.schedule_type == ScheduleType.CRON:
            if not schedule.cron_expression:
                raise ValueError("Cron schedule requires cron_expression")
            
            # Validate cron expression
            try:
                croniter(schedule.cron_expression)
            except Exception as e:
                raise ValueError(f"Invalid cron expression: {e}")
        
        # Validate time range
        if schedule.start_time and schedule.end_time:
            if schedule.end_time <= schedule.start_time:
                raise ValueError("end_time must be after start_time")
    
    async def _trigger_event(self, event: str, schedule: ScheduledTransfer, result: ExecutionResult):
        """Trigger an event"""
        if event in self.event_handlers:
            try:
                await self.event_handlers[event](schedule, result)
            except Exception as e:
                logger.error(f"Error in event handler {event}: {e}", exc_info=True)


# Example usage
if __name__ == "__main__":
    from mft_application import TransferConfig, TransferProtocol
    
    async def main():
        # Initialize MFT application
        mft_app = MFTApplication()
        
        # Initialize scheduler
        scheduler = TransferScheduler(mft_app)
        
        # Register event handler
        async def on_execution_failure(schedule, result):
            print(f"Execution failed: {schedule.name} - {result.error_message}")
        
        scheduler.register_event_handler('execution_failure', on_execution_failure)
        
        # Create a recurring schedule
        config = TransferConfig(
            protocol=TransferProtocol.SFTP,
            host="sftp.example.com",
            port=22,
            username="user",
            encryption_enabled=True
        )
        
        schedule = ScheduledTransfer(
            schedule_id=str(uuid.uuid4()),
            name="Daily Backup",
            description="Daily backup of important MFT_PRO",
            schedule_type=ScheduleType.RECURRING,
            source_path="/data/backup.tar.gz",
            destination_path="/remote/backups/backup.tar.gz",
            config=config,
            interval_minutes=1440,  # Daily
            notification_on_failure=True
        )
        
        scheduler.add_schedule(schedule)
        
        # Start scheduler
        await scheduler.start()
        
        # Let it run for a while
        await asyncio.sleep(60)
        
        # Stop scheduler
        await scheduler.stop()
    
    asyncio.run(main())
