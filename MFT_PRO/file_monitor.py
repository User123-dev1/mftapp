"""
File Monitoring & Automated Transfer System
Watches source folders and automatically transfers new/modified files
"""

import os
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ScheduleType(Enum):
    """Schedule types for transfers"""
    ONCE = "once"                      # Execute once immediately
    RECURRING = "recurring"            # Repeat at intervals
    CRON = "cron"                      # Cron-based schedule
    ON_DEMAND = "on_demand"           # Manual trigger
    EVENT_DRIVEN = "event_driven"     # Triggered by file events


class ActionType(Enum):
    """Action types for file handling"""
    COPY = "copy"                                    # Copy file (keep original)
    MOVE = "move"                                    # Move file (delete immediately)
    MOVE_WITH_DELAY = "move_with_delay"             # Move file (delete after delay)


class TriggerType(Enum):
    """Types of triggers for automated transfers"""
    FILE_CREATED = "file_created"      # New file appears
    FILE_MODIFIED = "file_modified"    # File is modified
    FILE_MOVED = "file_moved"          # File is moved/renamed
    SCHEDULED = "scheduled"            # Time-based (cron)
    ON_DEMAND = "on_demand"           # Manual trigger


@dataclass
class TransferRule:
    """Configuration for automated transfer rule"""
    # Required fields (no defaults)
    rule_id: str
    name: str
    source_path: str              # Path to monitor
    destination_path: str
    protocol: str                 # unc, smb, sftp
    host: str
    port: int

    # Optional fields (with defaults) - MUST come after required fields
    enabled: bool = True
    source_pattern: str = "*"     # File pattern (*.txt, *.*, specific_file.doc)
    username: Optional[str] = None
    password: Optional[str] = None

    # Schedule and trigger configuration
    schedule_type: ScheduleType = ScheduleType.EVENT_DRIVEN
    trigger_type: TriggerType = TriggerType.FILE_CREATED
    action_type: ActionType = ActionType.COPY

    # Advanced options
    overwrite_existing: bool = True
    min_file_size: int = 0        # Minimum file size in bytes
    max_file_size: int = 0        # Maximum file size (0 = no limit)
    file_age_seconds: int = 5     # Wait before transfer (file stability)
    delete_delay_seconds: int = 0  # Delay before deleting (for MOVE_WITH_DELAY)

    # Schedule options (if schedule_type is RECURRING or CRON)
    schedule_cron: Optional[str] = None           # Cron expression: "0 */4 * * *"
    schedule_interval_minutes: Optional[int] = None  # For RECURRING: 60 = hourly

    # Statistics
    files_transferred: int = 0
    bytes_transferred: int = 0  # Total bytes transferred
    last_transfer_time: Optional[datetime] = None
    status: str = "idle"  # idle, monitoring, transferring, error
    created_at: Optional[datetime] = None
    last_run: Optional[datetime] = None

    def __post_init__(self):
        """Set created_at timestamp if not provided"""
        if self.created_at is None:
            self.created_at = datetime.now()


class FileMonitorHandler(FileSystemEventHandler):
    """Handles file system events and triggers transfers"""

    def __init__(self, rule: TransferRule, mft_app, transfer_callback):
        self.rule = rule
        self.mft_app = mft_app
        self.transfer_callback = transfer_callback
        self.pending_files = {}  # Files waiting for stability check
        self.transferring_files = set()  # Files currently being transferred

    def _matches_pattern(self, filename: str) -> bool:
        """Check if filename matches rule pattern"""
        import fnmatch
        return fnmatch.fnmatch(filename, self.rule.source_pattern)

    def _should_transfer(self, file_path: str) -> bool:
        """Check if file should be transferred"""
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return False

            # Check if it's a file (not directory)
            if not os.path.isfile(file_path):
                return False

            # Check file pattern
            filename = os.path.basename(file_path)
            if not self._matches_pattern(filename):
                logger.debug(f"File {filename} doesn't match pattern {self.rule.source_pattern}")
                return False

            # Check file size - CRITICAL: Reject 0-byte files
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.info(f"⚠️ Skipping empty file (0 bytes): {filename}")
                return False

            if self.rule.min_file_size > 0 and file_size < self.rule.min_file_size:
                logger.debug(f"File {filename} too small: {file_size} < {self.rule.min_file_size}")
                return False

            if self.rule.max_file_size > 0 and file_size > self.rule.max_file_size:
                logger.debug(f"File {filename} too large: {file_size} > {self.rule.max_file_size}")
                return False

            # Content validation for specific file types
            if not self._validate_file_content(file_path, filename):
                return False

            # Check if already transferring
            if file_path in self.transferring_files:
                logger.debug(f"File {filename} already being transferred")
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking if should transfer {file_path}: {e}")
            return False

    def _validate_file_content(self, file_path: str, filename: str) -> bool:
        """Validate file has actual content based on file type"""
        try:
            # Get file extension
            _, ext = os.path.splitext(filename.lower())

            # CSV validation - must have data rows, not just headers or empty lines
            if ext == '.csv':
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()

                        # Remove empty lines and whitespace-only lines
                        non_empty_lines = [line.strip() for line in lines if line.strip()]

                        if len(non_empty_lines) == 0:
                            logger.info(f"⚠️ Skipping CSV with no content: {filename}")
                            return False

                        # Check if CSV has data beyond just headers
                        # A valid CSV should have at least 2 lines (header + 1 data row)
                        if len(non_empty_lines) < 2:
                            logger.info(f"⚠️ Skipping CSV with only headers (no data rows): {filename} ({len(non_empty_lines)} lines)")
                            return False

                        logger.info(f"✅ CSV validation passed: {filename} ({len(non_empty_lines)} lines)")
                        return True
                except Exception as e:
                    logger.warning(f"⚠️ Failed to validate CSV content for {filename}: {e}")
                    return False

            # PDF validation - check for PDF signature and minimum viable size
            elif ext == '.pdf':
                try:
                    # PDF files must start with %PDF- header
                    with open(file_path, 'rb') as f:
                        header = f.read(5)
                        if header != b'%PDF-':
                            logger.info(f"⚠️ Skipping invalid PDF (missing signature): {filename}")
                            return False

                        # Read entire file to check for EOF marker
                        f.seek(0)
                        content = f.read()

                        # Valid PDF should have %%EOF at the end
                        if b'%%EOF' not in content:
                            logger.info(f"⚠️ Skipping incomplete PDF (missing EOF): {filename}")
                            return False

                        # Check if PDF has some minimal content
                        # A valid PDF with actual content should be at least 1KB
                        if len(content) < 1024:
                            logger.info(f"⚠️ Skipping PDF with minimal content: {filename} ({len(content)} bytes)")
                            return False

                        logger.info(f"✅ PDF validation passed: {filename} ({len(content)} bytes)")
                        return True
                except Exception as e:
                    logger.warning(f"⚠️ Failed to validate PDF content for {filename}: {e}")
                    return False

            # For other file types, basic size check is sufficient
            else:
                return True

        except Exception as e:
            logger.error(f"❌ Error validating file content for {filename}: {e}")
            # On error, allow transfer (fail open)
            return True

    def _schedule_transfer(self, file_path: str):
        """Schedule file for transfer after stability check"""
        logger.info(f"📋 Scheduling transfer for: {file_path}")
        self.pending_files[file_path] = time.time()

        # Start stability checker thread
        def check_and_transfer():
            max_retries = 10  # Maximum retries for empty files waiting to be populated
            retry_count = 0

            time.sleep(self.rule.file_age_seconds)

            # Check if file is stable (size hasn't changed)
            if file_path in self.pending_files:
                try:
                    while retry_count < max_retries:
                        if os.path.exists(file_path):
                            initial_size = os.path.getsize(file_path)

                            # Special handling for 0-byte files (CSV scenario)
                            if initial_size == 0:
                                retry_count += 1
                                logger.info(f"⏳ File is empty (0 bytes), waiting for population... (retry {retry_count}/{max_retries})")
                                time.sleep(2)  # Wait 2 seconds for file to be populated
                                continue

                            # Check if file size is stable
                            time.sleep(1)
                            if os.path.exists(file_path):
                                final_size = os.path.getsize(file_path)

                                if initial_size == final_size and final_size > 0:
                                    logger.info(f"✅ File stable ({final_size:,} bytes), initiating transfer: {file_path}")
                                    del self.pending_files[file_path]
                                    self._execute_transfer(file_path)
                                    return
                                else:
                                    logger.info(f"⏳ File still changing ({initial_size} -> {final_size} bytes), will retry...")
                                    retry_count += 1
                                    time.sleep(1)
                            else:
                                logger.warning(f"⚠️ File disappeared: {file_path}")
                                if file_path in self.pending_files:
                                    del self.pending_files[file_path]
                                return
                        else:
                            logger.warning(f"⚠️ File not found: {file_path}")
                            if file_path in self.pending_files:
                                del self.pending_files[file_path]
                            return

                    # Max retries reached
                    logger.error(f"❌ File still empty after {max_retries} retries, giving up: {file_path}")
                    if file_path in self.pending_files:
                        del self.pending_files[file_path]

                except Exception as e:
                    logger.error(f"❌ Error in stability check: {e}")
                    if file_path in self.pending_files:
                        del self.pending_files[file_path]

        thread = threading.Thread(target=check_and_transfer, daemon=True)
        thread.start()

    def _execute_transfer(self, file_path: str):
        """Execute the actual file transfer"""
        try:
            self.transferring_files.add(file_path)
            filename = os.path.basename(file_path)

            # Build destination path
            dest_path = os.path.join(self.rule.destination_path, filename)

            logger.info(f"\n{'='*80}")
            logger.info(f"🚀 AUTO-TRANSFER TRIGGERED")
            logger.info(f"{'='*80}")
            logger.info(f"📋 Rule: {self.rule.name}")
            logger.info(f"📂 Source: {file_path}")
            logger.info(f"📂 Dest: {dest_path}")
            logger.info(f"⚙️  Protocol: {self.rule.protocol}")
            logger.info(f"{'='*80}\n")

            # Check if this is a local-to-local transfer
            is_local_source = os.path.exists(file_path) and (file_path[1:3] == ':\\' or not file_path.startswith('//'))
            is_local_dest = dest_path[1:3] == ':\\' or not dest_path.startswith('//')

            if is_local_source and is_local_dest and self.rule.protocol == "local":
                # Handle local transfer directly
                logger.info("🔵 LOCAL FILE TRANSFER (Direct Copy)")
                logger.info(f"   Source exists: {os.path.exists(file_path)}")

                try:
                    # Create destination directory if needed
                    dest_dir = os.path.dirname(dest_path)
                    os.makedirs(dest_dir, exist_ok=True)
                    logger.info(f"   Dest directory: {dest_dir}")

                    # Copy file
                    import shutil
                    shutil.copy2(file_path, dest_path)
                    logger.info(f"   ✅ File copied successfully")

                    # Verify
                    if os.path.exists(dest_path):
                        src_size = os.path.getsize(file_path)
                        dst_size = os.path.getsize(dest_path)
                        logger.info(f"   Source size: {src_size} bytes")
                        logger.info(f"   Dest size: {dst_size} bytes")

                        if src_size == dst_size:
                            logger.info(f"   ✅ Size verification PASSED")
                            logger.info(f"\n{'='*80}")
                            logger.info(f"✅ LOCAL TRANSFER SUCCESSFUL!")
                            logger.info(f"{'='*80}\n")

                            # Update statistics
                            self.rule.files_transferred += 1
                            self.rule.bytes_transferred += src_size
                            self.rule.last_transfer_time = datetime.now()
                            self.rule.status = "monitoring"

                            # Handle file based on action type
                            self._handle_post_transfer_action(file_path)

                            # CRITICAL: Exit immediately - do NOT call MFT application
                            return
                        else:
                            logger.error(f"   ❌ Size mismatch: {src_size} vs {dst_size}")
                    else:
                        logger.error(f"   ❌ Destination file not found after copy")

                    logger.error(f"\n{'='*80}")
                    logger.error(f"❌ LOCAL TRANSFER FAILED!")
                    logger.error(f"{'='*80}\n")
                    self.rule.status = "error"
                    return

                except Exception as e:
                    logger.error(f"\n{'='*80}")
                    logger.error(f"❌ LOCAL TRANSFER EXCEPTION: {e}")
                    logger.error(f"{'='*80}\n")
                    import traceback
                    traceback.print_exc()
                    self.rule.status = "error"
                    return

            # For network transfers, use MFT application
            transfer_success = False
            try:
                asyncio.run(self.transfer_callback(
                    source_path=file_path,
                    destination_path=dest_path,
                    rule=self.rule
                ))
                transfer_success = True
                logger.info("✅ Network transfer completed successfully")
            except Exception as transfer_error:
                logger.error(f"❌ Network transfer failed: {transfer_error}")
                transfer_success = False

            # Only update statistics and perform actions if transfer was successful
            if transfer_success:
                # Update statistics
                try:
                    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                    self.rule.files_transferred += 1
                    self.rule.bytes_transferred += file_size
                except:
                    self.rule.files_transferred += 1

                self.rule.last_transfer_time = datetime.now()
                self.rule.status = "monitoring"

                # Handle file based on action type
                self._handle_post_transfer_action(file_path)
            else:
                logger.warning(f"⚠️ Transfer failed - source file kept: {file_path}")
                self.rule.status = "error"

        except Exception as e:
            logger.error(f"❌ Transfer failed: {e}")
            import traceback
            traceback.print_exc()
            self.rule.status = "error"
        finally:
            if file_path in self.transferring_files:
                self.transferring_files.remove(file_path)

    def _handle_post_transfer_action(self, file_path: str):
        """Handle file after successful transfer based on action type"""
        # Handle file based on action type
        if self.rule.action_type == ActionType.MOVE:
            # Delete source file immediately
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Deleted source file (MOVE): {file_path}")
            except Exception as e:
                logger.error(f"❌ Failed to delete source file: {e}")

        elif self.rule.action_type == ActionType.MOVE_WITH_DELAY:
            # Schedule deletion after delay
            def delayed_delete():
                time.sleep(self.rule.delete_delay_seconds)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"🗑️ Deleted source file after {self.rule.delete_delay_seconds}s delay: {file_path}")
                except Exception as e:
                    logger.error(f"❌ Failed to delete source file: {e}")

            thread = threading.Thread(target=delayed_delete, daemon=True)
            thread.start()
            logger.info(f"⏰ Scheduled deletion in {self.rule.delete_delay_seconds}s: {file_path}")

        elif self.rule.action_type == ActionType.COPY:
            # Keep source file (default)
            logger.info(f"📋 Source file kept (COPY): {file_path}")

    def on_created(self, event):
        """Called when a file is created"""
        if event.is_directory:
            return

        if self.rule.trigger_type != TriggerType.FILE_CREATED:
            return

        if self._should_transfer(event.src_path):
            logger.info(f"📄 New file detected: {event.src_path}")
            self._schedule_transfer(event.src_path)

    def on_modified(self, event):
        """Called when a file is modified"""
        if event.is_directory:
            return

        if self.rule.trigger_type != TriggerType.FILE_MODIFIED:
            return

        if self._should_transfer(event.src_path):
            logger.info(f"📝 File modified: {event.src_path}")
            self._schedule_transfer(event.src_path)

    def on_moved(self, event):
        """Called when a file is moved"""
        if event.is_directory:
            return

        if self.rule.trigger_type != TriggerType.FILE_MOVED:
            return

        if self._should_transfer(event.dest_path):
            logger.info(f"📦 File moved: {event.dest_path}")
            self._schedule_transfer(event.dest_path)


class FileMonitorManager:
    """Manages multiple file monitors and transfer rules"""

    def __init__(self, mft_app):
        self.mft_app = mft_app
        self.rules: Dict[str, TransferRule] = {}
        self.observers: Dict[str, Observer] = {}
        self.active = False

    def add_rule(self, rule: TransferRule):
        """Add a new transfer rule"""
        logger.info(f"➕ Adding rule: {rule.name} (ID: {rule.rule_id})")
        self.rules[rule.rule_id] = rule

        if rule.enabled and rule.schedule_type == ScheduleType.EVENT_DRIVEN:
            # Start monitoring for event-driven rules
            self._start_monitoring(rule)

    def remove_rule(self, rule_id: str):
        """Remove a transfer rule"""
        if rule_id in self.rules:
            logger.info(f"➖ Removing rule: {rule_id}")
            self._stop_monitoring(rule_id)
            del self.rules[rule_id]

    def enable_rule(self, rule_id: str):
        """Enable a transfer rule"""
        if rule_id in self.rules:
            rule = self.rules[rule_id]
            rule.enabled = True
            logger.info(f"✅ Enabled rule: {rule.name}")

            if rule.schedule_type == ScheduleType.EVENT_DRIVEN:
                self._start_monitoring(rule)

    def disable_rule(self, rule_id: str):
        """Disable a transfer rule"""
        if rule_id in self.rules:
            rule = self.rules[rule_id]
            rule.enabled = False
            logger.info(f"❌ Disabled rule: {rule.name}")
            self._stop_monitoring(rule_id)

    def _start_monitoring(self, rule: TransferRule):
        """Start monitoring a folder for a rule"""
        if rule.rule_id in self.observers:
            return  # Already monitoring

        try:
            # Normalize path
            source_path = rule.source_path.replace('//', '\\\\').replace('/', '\\')

            # Convert C$ to C: for Windows paths
            import re
            source_path = re.sub(r'\\([A-Za-z])\$\\', r'\\\1:\\', source_path)

            # If source is a UNC path pointing to localhost, convert to local path
            if source_path.startswith('\\\\'):
                import socket
                # Extract host from UNC path (\\host\path)
                unc_parts = source_path.lstrip('\\').split('\\', 1)
                if len(unc_parts) >= 1:
                    source_host = unc_parts[0]
                    source_path_part = unc_parts[1] if len(unc_parts) > 1 else ''

                    # Get local machine IPs and hostname
                    local_hostname = socket.gethostname()
                    local_ips = [local_hostname.lower()]
                    try:
                        for ip_info in socket.getaddrinfo(local_hostname, None):
                            ip = ip_info[4][0]
                            local_ips.append(ip)
                    except:
                        pass

                    # Check if source_host is the local machine
                    is_local = (
                        source_host.lower() in ['localhost', '127.0.0.1', local_hostname.lower()] or
                        source_host in local_ips
                    )

                    if is_local:
                        # This is a local UNC path - convert to local drive path
                        source_path = source_path_part
                        logger.info(f"   ✅ UNC points to localhost - converted to local: {source_path}")

            # Check if path exists
            if not os.path.exists(source_path):
                logger.error(f"❌ Source path does not exist: {source_path}")
                logger.error(f"   Original: {rule.source_path}")
                logger.error(f"   Normalized: {source_path}")
                return

            # Create event handler
            event_handler = FileMonitorHandler(
                rule=rule,
                mft_app=self.mft_app,
                transfer_callback=self._transfer_callback
            )

            # Create observer
            observer = Observer()
            observer.schedule(event_handler, source_path, recursive=False)
            observer.start()

            self.observers[rule.rule_id] = observer
            rule.status = "monitoring"

            logger.info(f"👀 Started monitoring: {source_path}")
            logger.info(f"   Pattern: {rule.source_pattern}")
            logger.info(f"   Trigger: {rule.trigger_type.value}")

        except Exception as e:
            logger.error(f"❌ Failed to start monitoring: {e}")
            import traceback
            traceback.print_exc()

    def _stop_monitoring(self, rule_id: str):
        """Stop monitoring for a rule"""
        if rule_id in self.observers:
            observer = self.observers[rule_id]
            observer.stop()
            observer.join()
            del self.observers[rule_id]

            if rule_id in self.rules:
                self.rules[rule_id].status = "idle"

            logger.info(f"🛑 Stopped monitoring for rule: {rule_id}")

    async def _transfer_callback(self, source_path: str, destination_path: str, rule: TransferRule):
        """Callback for executing transfers"""
        from mft_application import TransferConfig, TransferProtocol

        # Convert protocol string to enum
        protocol_map = {
            'unc': TransferProtocol.UNC,
            'smb': TransferProtocol.SMB,
            'sftp': TransferProtocol.SFTP,
            'ftp': TransferProtocol.FTP,
            'ftps': TransferProtocol.FTPS,
            'http': TransferProtocol.HTTP,
            'https': TransferProtocol.HTTPS,
        }

        protocol_enum = protocol_map.get(rule.protocol.lower(), TransferProtocol.UNC)

        # Create transfer config
        config = TransferConfig(
            protocol=protocol_enum,
            host=rule.host,
            port=rule.port,
            username=rule.username,
            password=rule.password,
            encryption_enabled=True,
            retry_count=3,
            retry_delay=5,
            timeout=300
        )

        # Normalize destination path
        if not destination_path.startswith('//') and not destination_path.startswith('\\\\'):
            if protocol_enum in [TransferProtocol.SMB, TransferProtocol.UNC]:
                if not destination_path.startswith('/'):
                    destination_path = f"//{rule.host}/{destination_path}"
                else:
                    destination_path = f"//{rule.host}{destination_path}"

        rule.status = "transferring"

        try:
            # Execute transfer
            task_id = await self.mft_app.transfer_file(source_path, destination_path, config)
            logger.info(f"✅ Transfer initiated: {task_id}")

            # Wait for completion
            max_wait = 60
            elapsed = 0
            poll_interval = 0.5

            while elapsed < max_wait:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                status = self.mft_app.get_transfer_status(task_id)
                if status:
                    current_status = status.get('status', 'unknown')

                    if current_status == 'completed':
                        logger.info(f"✅ AUTO-TRANSFER COMPLETED!")
                        rule.status = "monitoring"
                        return True
                    elif current_status == 'failed':
                        error_msg = status.get('error_message', 'Unknown error')
                        logger.error(f"❌ AUTO-TRANSFER FAILED: {error_msg}")
                        rule.status = "error"
                        return False

            logger.warning(f"⚠️ Transfer timeout after {max_wait}s")
            rule.status = "monitoring"
            return False

        except Exception as e:
            logger.error(f"❌ Transfer error: {e}")
            import traceback
            traceback.print_exc()
            rule.status = "error"
            return False

    def process_existing_files(self, rule_id: str):
        """Process existing files in source folder for a rule"""
        if rule_id not in self.rules:
            logger.error(f"Rule not found: {rule_id}")
            return

        rule = self.rules[rule_id]

        if rule.schedule_type != ScheduleType.EVENT_DRIVEN:
            logger.info(f"Skipping existing files for non-event-driven rule: {rule.name}")
            return

        try:
            import fnmatch
            import re
            import socket

            # Normalize path
            source_path = rule.source_path.replace('//', '\\\\').replace('/', '\\')

            # Convert C$ to C: for Windows paths
            source_path = re.sub(r'\\([A-Za-z])\$\\', r'\\\1:\\', source_path)

            # If source is a UNC path pointing to localhost, convert to local path
            if source_path.startswith('\\\\'):
                # Extract host from UNC path (\\host\path)
                unc_parts = source_path.lstrip('\\').split('\\', 1)
                if len(unc_parts) >= 1:
                    source_host = unc_parts[0]
                    source_path_part = unc_parts[1] if len(unc_parts) > 1 else ''

                    # Get local machine IPs and hostname
                    local_hostname = socket.gethostname()
                    local_ips = [local_hostname.lower()]
                    try:
                        for ip_info in socket.getaddrinfo(local_hostname, None):
                            ip = ip_info[4][0]
                            local_ips.append(ip)
                    except:
                        pass

                    # Check if source_host is the local machine
                    is_local = (
                        source_host.lower() in ['localhost', '127.0.0.1', local_hostname.lower()] or
                        source_host in local_ips
                    )

                    if is_local:
                        # This is a local UNC path - convert to local drive path
                        source_path = source_path_part
                        logger.info(f"   ✅ UNC points to localhost - converted to local: {source_path}")

            if not os.path.exists(source_path):
                logger.warning(f"⚠️ Source path does not exist: {source_path}")
                logger.warning(f"   Original: {rule.source_path}")
                logger.warning(f"   Normalized: {source_path}")
                return

            if not os.path.isdir(source_path):
                logger.warning(f"⚠️ Source path is not a directory: {source_path}")
                return

            logger.info(f"\n{'='*80}")
            logger.info(f"📂 PROCESSING EXISTING FILES")
            logger.info(f"{'='*80}")
            logger.info(f"Rule: {rule.name}")
            logger.info(f"Source: {source_path}")
            logger.info(f"Pattern: {rule.source_pattern}")
            logger.info(f"{'='*80}\n")

            # Get handler if monitoring is active
            if rule_id in self.observers:
                observer = self.observers[rule_id]
                handlers = observer.emitters[0]._handlers

                if handlers:
                    handler = handlers[0]

                    # Process all files in source directory
                    file_count = 0
                    for filename in os.listdir(source_path):
                        file_path = os.path.join(source_path, filename)

                        # Check if it's a file
                        if os.path.isfile(file_path):
                            # Check if matches pattern
                            if fnmatch.fnmatch(filename, rule.source_pattern):
                                logger.info(f"   ✅ Found: {filename}")
                                handler._schedule_transfer(file_path)
                                file_count += 1
                            else:
                                logger.debug(f"   ⏭️ Skipped (pattern): {filename}")

                    logger.info(f"\n✅ Processed {file_count} existing files\n")
                else:
                    logger.error("❌ No handlers found for observer")
            else:
                logger.error(f"❌ No observer found for rule: {rule_id}")

        except Exception as e:
            logger.error(f"❌ Error processing existing files: {e}")
            import traceback
            traceback.print_exc()

    def start_all(self):
        """Start all enabled rules"""
        logger.info(f"\n{'='*80}")
        logger.info(f"🚀 STARTING FILE MONITOR MANAGER")
        logger.info(f"{'='*80}\n")

        self.active = True

        for rule in self.rules.values():
            if rule.enabled and rule.schedule_type == ScheduleType.EVENT_DRIVEN:
                self._start_monitoring(rule)

        logger.info(f"✅ File Monitor Manager started with {len(self.observers)} active monitors")

    def stop_all(self):
        """Stop all monitors"""
        logger.info(f"🛑 Stopping all file monitors...")

        for rule_id in list(self.observers.keys()):
            self._stop_monitoring(rule_id)

        self.active = False
        logger.info(f"✅ All monitors stopped")

    def get_statistics(self):
        """Get statistics for all rules"""
        stats = {
            'total_rules': len(self.rules),
            'active_rules': len([r for r in self.rules.values() if r.enabled]),
            'monitoring_rules': len(self.observers),
            'total_files_transferred': sum(r.files_transferred for r in self.rules.values()),
            'total_bytes_transferred': sum(r.bytes_transferred for r in self.rules.values()),
            'rules': []
        }

        for rule in self.rules.values():
            rule_stats = {
                'rule_id': rule.rule_id,
                'name': rule.name,
                'enabled': rule.enabled,
                'status': rule.status,
                'source_path': rule.source_path,
                'source_pattern': rule.source_pattern,
                'destination_path': rule.destination_path,
                'protocol': rule.protocol,
                'schedule_type': rule.schedule_type.value,
                'trigger_type': rule.trigger_type.value,
                'action_type': rule.action_type.value,
                'files_transferred': rule.files_transferred,
                'bytes_transferred': rule.bytes_transferred,
                'last_transfer': rule.last_transfer_time.isoformat() if rule.last_transfer_time else None
            }
            stats['rules'].append(rule_stats)

        return stats


# Example usage
if __name__ == "__main__":
    # This would be integrated with your MFT application
    print("File Monitor Manager - Ready for integration")
    print("\nFeatures:")
    print("  ✅ Real-time file monitoring")
    print("  ✅ Automated transfers on file creation/modification")
    print("  ✅ File pattern matching (*.txt, *.*, etc.)")
    print("  ✅ File stability checking (wait for complete writes)")
    print("  ✅ Multiple concurrent rules")
    print("  ✅ Per-rule statistics")
    print("  ✅ Enable/disable rules on the fly")