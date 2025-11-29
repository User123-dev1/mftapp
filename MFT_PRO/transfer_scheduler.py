"""
Transfer Rule Scheduler
Executes transfer rules based on their schedule configuration
"""

import asyncio
import logging
import os
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from croniter import croniter
import threading

from database import (
    get_database, TransferRule, Device, TransferQueue,
    DeletionDelay
)
from mft_application import MFTApplication, TransferConfig, TransferProtocol

logger = logging.getLogger(__name__)


class TransferScheduler:
    """Manages scheduled execution of transfer rules"""
    
    def __init__(self, mft_app: MFTApplication):
        self.mft_app = mft_app
        self.db = get_database()
        self.running = False
        self.scheduler_task = None
        self.check_interval = 30  # Check every 30 seconds
        self.active_executions: Dict[int, asyncio.Task] = {}
        
    async def start(self):
        """Start the scheduler"""
        if self.running:
            logger.warning("Scheduler already running")
            return
        
        self.running = True
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Transfer scheduler started")
    
    async def stop(self):
        """Stop the scheduler"""
        self.running = False
        
        # Cancel scheduler task
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        
        # Cancel active executions
        for task in self.active_executions.values():
            task.cancel()
        
        # Wait for all to complete
        if self.active_executions:
            await asyncio.gather(*self.active_executions.values(), return_exceptions=True)
        
        logger.info("Transfer scheduler stopped")
    
    async def _scheduler_loop(self):
        """Main scheduler loop"""
        while self.running:
            try:
                # Get all enabled rules
                rules = self.db.get_transfer_rules(enabled_only=True)
                
                for rule in rules:
                    # Skip if already executing
                    if rule.id in self.active_executions:
                        continue
                    
                    # Check if rule should execute
                    if self._should_execute(rule):
                        logger.info(f"Executing transfer rule: {rule.name} (ID: {rule.id})")
                        
                        # Create execution task
                        task = asyncio.create_task(self._execute_rule(rule))
                        self.active_executions[rule.id] = task
                
                # Clean up completed tasks
                completed_rule_ids = []
                for rule_id, task in self.active_executions.items():
                    if task.done():
                        completed_rule_ids.append(rule_id)
                
                for rule_id in completed_rule_ids:
                    del self.active_executions[rule_id]
                
                # Wait before next check
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    def _should_execute(self, rule: TransferRule) -> bool:
        """Check if a rule should execute now"""
        if rule.schedule_type == "on_demand":
            # On-demand rules don't auto-execute
            return False
        
        elif rule.schedule_type == "interval":
            # Check if enough time has passed since last execution
            if not rule.schedule_interval:
                return False
            
            # Get last execution time from metadata
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(timestamp) as last_execution
                FROM audit_log
                WHERE resource_type = 'transfer_rule' 
                  AND resource_id = ?
                  AND action = 'execute'
                  AND status = 'success'
            """, (str(rule.id),))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or not row['last_execution']:
                # Never executed, run it
                return True
            
            last_execution = datetime.fromisoformat(row['last_execution'])
            next_execution = last_execution + timedelta(seconds=rule.schedule_interval)
            
            return datetime.utcnow() >= next_execution
        
        elif rule.schedule_type == "cron":
            # Check if cron schedule matches
            if not rule.cron_expression:
                return False
            
            try:
                # Get last execution
                conn = self.db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT MAX(timestamp) as last_execution
                    FROM audit_log
                    WHERE resource_type = 'transfer_rule' 
                      AND resource_id = ?
                      AND action = 'execute'
                      AND status = 'success'
                """, (str(rule.id),))
                
                row = cursor.fetchone()
                conn.close()
                
                last_execution = None
                if row and row['last_execution']:
                    last_execution = datetime.fromisoformat(row['last_execution'])
                
                # Check if next cron time has passed
                cron = croniter(rule.cron_expression, last_execution or datetime.utcnow())
                next_execution = cron.get_next(datetime)
                
                return datetime.utcnow() >= next_execution
                
            except Exception as e:
                logger.error(f"Error parsing cron expression '{rule.cron_expression}': {e}")
                return False
        
        return False
    
    async def _execute_rule(self, rule: TransferRule):
        """Execute a transfer rule"""
        try:
            logger.info(f"Executing rule {rule.id}: {rule.name}")
            
            # Get source and destination devices
            source_device = self.db.get_device(rule.source_device_id)
            dest_device = self.db.get_device(rule.destination_device_id)
            
            if not source_device or not dest_device:
                logger.error(f"Rule {rule.id}: Source or destination device not found")
                return
            
            # Check if devices are online
            if source_device.status == "offline":
                logger.warning(f"Rule {rule.id}: Source device {source_device.name} is offline, queueing transfers")
                # Queue for later
                await self._queue_pending_transfers(rule, source_device)
                return
            
            if dest_device.status == "offline":
                logger.warning(f"Rule {rule.id}: Destination device {dest_device.name} is offline, queueing transfers")
                await self._queue_pending_transfers(rule, source_device)
                return
            
            # Find files matching pattern
            files_to_transfer = self._find_matching_files(rule.source_path, rule.file_pattern)
            
            if not files_to_transfer:
                logger.debug(f"Rule {rule.id}: No files found matching pattern {rule.file_pattern}")
                return
            
            logger.info(f"Rule {rule.id}: Found {len(files_to_transfer)} files to transfer")
            
            # Transfer each file
            successful_transfers = 0
            failed_transfers = 0
            
            for source_file in files_to_transfer:
                try:
                    # Build destination path
                    filename = os.path.basename(source_file)
                    dest_path = os.path.join(rule.destination_path, filename)
                    
                    # Create transfer config
                    config = self._create_transfer_config(source_device, dest_device, rule)
                    
                    # Execute transfer
                    task_id = await self.mft_app.transfer_file(
                        source_path=source_file,
                        destination_path=dest_path,
                        config=config,
                        metadata={
                            'rule_id': rule.id,
                            'rule_name': rule.name,
                            'transfer_mode': rule.transfer_mode
                        }
                    )
                    
                    # Wait for transfer to complete
                    while True:
                        status = self.mft_app.get_transfer_status(task_id)
                        if status and status['status'] in ['completed', 'failed']:
                            break
                        await asyncio.sleep(1)
                    
                    if status['status'] == 'completed':
                        successful_transfers += 1
                        
                        # Handle post-transfer actions
                        if rule.transfer_mode == "move":
                            await self._handle_move_operation(rule, source_file, status)
                    else:
                        failed_transfers += 1
                        logger.error(f"Transfer failed for {source_file}: {status.get('error_message')}")
                    
                except Exception as e:
                    failed_transfers += 1
                    logger.error(f"Error transferring {source_file}: {e}", exc_info=True)
            
            # Log execution
            self.db.log_audit(
                username="scheduler",
                action="execute",
                resource_type="transfer_rule",
                resource_id=str(rule.id),
                details={
                    'files_found': len(files_to_transfer),
                    'successful_transfers': successful_transfers,
                    'failed_transfers': failed_transfers
                },
                status="success" if failed_transfers == 0 else "partial"
            )
            
            logger.info(f"Rule {rule.id} execution complete: {successful_transfers} succeeded, {failed_transfers} failed")
            
        except Exception as e:
            logger.error(f"Error executing rule {rule.id}: {e}", exc_info=True)
            self.db.log_audit(
                username="scheduler",
                action="execute",
                resource_type="transfer_rule",
                resource_id=str(rule.id),
                details={'error': str(e)},
                status="failure"
            )
    
    def _find_matching_files(self, base_path: str, pattern: str) -> List[str]:
        """Find files matching the pattern"""
        try:
            # Handle local paths
            if os.path.exists(base_path):
                if os.path.isfile(base_path):
                    return [base_path]
                
                # Directory - search for matching files
                search_pattern = os.path.join(base_path, pattern)
                files = glob.glob(search_pattern, recursive=False)
                
                # Filter to files only
                return [f for f in files if os.path.isfile(f)]
            
            # For remote paths, we'll need to query the device
            # This is simplified - in production, you'd connect to the device
            logger.warning(f"Remote path scanning not implemented: {base_path}")
            return []
            
        except Exception as e:
            logger.error(f"Error finding files in {base_path}: {e}")
            return []
    
    def _create_transfer_config(self, source_device: Device, dest_device: Device, 
                               rule: TransferRule) -> TransferConfig:
        """Create transfer configuration from devices and rule"""
        # Use destination device for transfer (we're pushing to it)
        protocol = TransferProtocol(dest_device.protocol)
        
        config = TransferConfig(
            protocol=protocol,
            host=dest_device.hostname,
            port=dest_device.port,
            username=dest_device.username,
            password=dest_device.password,
            private_key_path=dest_device.private_key_path,
            encryption_enabled=True,
            retry_count=rule.max_retries if rule.retry_on_failure else 0,
            retry_delay=5,
            timeout=300
        )
        
        return config
    
    async def _handle_move_operation(self, rule: TransferRule, source_file: str, 
                                    transfer_status: Dict[str, Any]):
        """Handle post-transfer operations for MOVE mode"""
        try:
            # Get deletion delay
            delay = DeletionDelay(rule.deletion_delay)
            
            # Add to pending deletions
            self.db.add_pending_deletion(
                rule_id=rule.id,
                file_path=source_file,
                size=transfer_status.get('file_size', 0),
                checksum=transfer_status.get('checksum_sha256', ''),
                delay=delay
            )
            
            logger.info(f"Scheduled deletion of {source_file} with delay {delay.value}")
            
            # If immediate deletion, process now
            if delay == DeletionDelay.IMMEDIATE:
                await self._process_pending_deletions()
            
        except Exception as e:
            logger.error(f"Error handling move operation for {source_file}: {e}")
    
    async def _process_pending_deletions(self):
        """Process files pending deletion"""
        try:
            deletions = self.db.get_deletions_due()
            
            for deletion in deletions:
                try:
                    if os.path.exists(deletion.file_path):
                        os.remove(deletion.file_path)
                        self.db.mark_deleted(deletion.id, success=True)
                        logger.info(f"Deleted file: {deletion.file_path}")
                    else:
                        self.db.mark_deleted(deletion.id, success=True)
                        logger.warning(f"File already deleted: {deletion.file_path}")
                        
                except Exception as e:
                    logger.error(f"Error deleting {deletion.file_path}: {e}")
                    self.db.mark_deleted(deletion.id, success=False)
        
        except Exception as e:
            logger.error(f"Error processing pending deletions: {e}")
    
    async def _queue_pending_transfers(self, rule: TransferRule, source_device: Device):
        """Queue transfers when device is offline"""
        try:
            files = self._find_matching_files(rule.source_path, rule.file_pattern)
            
            for file_path in files:
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                self.db.queue_transfer(rule.id, file_path, file_size)
            
            logger.info(f"Queued {len(files)} transfers for rule {rule.id}")
            
        except Exception as e:
            logger.error(f"Error queueing transfers: {e}")
    
    async def execute_rule_now(self, rule_id: int):
        """Manually execute a rule immediately"""
        rule = None
        rules = self.db.get_transfer_rules()
        for r in rules:
            if r.id == rule_id:
                rule = r
                break
        
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        
        if not rule.enabled:
            raise ValueError(f"Rule {rule_id} is disabled")
        
        # Execute the rule
        await self._execute_rule(rule)
    
    async def process_transfer_queue(self):
        """Process queued transfers for rules"""
        try:
            queued_transfers = self.db.get_queued_transfers()
            
            for transfer in queued_transfers:
                # Get the rule
                rules = self.db.get_transfer_rules()
                rule = None
                for r in rules:
                    if r.id == transfer.transfer_rule_id:
                        rule = r
                        break
                
                if not rule or not rule.enabled:
                    continue
                
                # Check if destination device is online
                dest_device = self.db.get_device(rule.destination_device_id)
                if not dest_device or dest_device.status != "online":
                    continue
                
                # Try to execute the queued transfer
                try:
                    source_device = self.db.get_device(rule.source_device_id)
                    if not source_device:
                        continue
                    
                    filename = os.path.basename(transfer.source_file)
                    dest_path = os.path.join(rule.destination_path, filename)
                    
                    config = self._create_transfer_config(source_device, dest_device, rule)
                    
                    task_id = await self.mft_app.transfer_file(
                        source_path=transfer.source_file,
                        destination_path=dest_path,
                        config=config,
                        metadata={
                            'rule_id': rule.id,
                            'queued_transfer_id': transfer.id
                        }
                    )
                    
                    # Mark as processing
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE transfer_queue 
                        SET status = 'processing', last_attempt = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (transfer.id,))
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Processing queued transfer {transfer.id}")
                    
                except Exception as e:
                    logger.error(f"Error processing queued transfer {transfer.id}: {e}")
                    
                    # Update retry count
                    conn = self.db._get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE transfer_queue 
                        SET retry_count = retry_count + 1, 
                            last_attempt = CURRENT_TIMESTAMP,
                            error_message = ?
                        WHERE id = ?
                    """, (str(e), transfer.id))
                    conn.commit()
                    conn.close()
        
        except Exception as e:
            logger.error(f"Error in process_transfer_queue: {e}")


# Global scheduler instance
_scheduler_instance: Optional[TransferScheduler] = None


def get_scheduler(mft_app: MFTApplication) -> TransferScheduler:
    """Get or create global scheduler instance"""
    global _scheduler_instance
    
    if _scheduler_instance is None:
        _scheduler_instance = TransferScheduler(mft_app)
    
    return _scheduler_instance
