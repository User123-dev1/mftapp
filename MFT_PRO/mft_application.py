"""
Simplified MFT Application Core
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass
import uuid
from datetime import datetime
import json

logger = logging.getLogger(__name__)

# Import activity logger
try:
    from activity_logger import activity_logger
except ImportError:
    activity_logger = None
    logger.warning("Activity logger not available")

# Import database
try:
    from database import get_database, FileTransfer
except ImportError:
    get_database = None
    FileTransfer = None
    logger.warning("Database not available - transfers will only be saved to JSON")


class TransferProtocol(Enum):
    """Supported transfer protocols"""
    SFTP = "sftp"
    FTP = "ftp"
    FTPS = "ftps"
    HTTP = "http"
    HTTPS = "https"
    WEBDAV = "webdav"
    SMB = "smb"
    UNC = "unc"
    TFTP = "tftp"
    AS2 = "as2"
    OFTP2 = "oftp2"
    AFTP = "aftp"


class TransferStatus(Enum):
    """Transfer status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ComplianceFramework(Enum):
    """Compliance frameworks"""
    HIPAA = "hipaa"
    GDPR = "gdpr"
    PCI_DSS = "pci_dss"


@dataclass
class TransferConfig:
    """Transfer configuration"""
    protocol: TransferProtocol
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    private_key_path: Optional[str] = None
    encryption_enabled: bool = True
    retry_count: int = 3
    retry_delay: int = 5
    timeout: int = 300
    compliance_frameworks: list = None


class MFTApplication:
    """Main MFT Application"""

    def __init__(self, state_manager=None):
        self.monitor = TransferMonitor(state_manager=state_manager)
        self.protocol_handlers = {}
        self._initialize_handlers()
        logger.info("MFT Application initialized")

        # Load saved transfer history
        if state_manager:
            self.monitor.load_state()
    
    def _initialize_handlers(self):
        """Initialize protocol handlers"""
        try:
            logger.info("🔧 Initializing protocol handlers...")
            logger.info("   Importing protocol_handlers module...")

            from protocol_handlers import (
                SFTPHandler, FTPSHandler, HTTPSHandler,
                WebDAVHandler, SMBHandler, UNCHandler,
                TFTPHandler, AS2Handler
            )

            logger.info("   ✅ Successfully imported all handler classes")
            logger.info("   Creating handler instances...")

            self.protocol_handlers = {
                TransferProtocol.SFTP: SFTPHandler(),
                TransferProtocol.FTPS: FTPSHandler(),
                TransferProtocol.FTP: FTPSHandler(),
                TransferProtocol.HTTPS: HTTPSHandler(),
                TransferProtocol.HTTP: HTTPSHandler(),
                TransferProtocol.WEBDAV: WebDAVHandler(),
                TransferProtocol.SMB: SMBHandler(),
                TransferProtocol.UNC: UNCHandler(),
                TransferProtocol.TFTP: TFTPHandler(),
                TransferProtocol.AS2: AS2Handler(),
            }
            logger.info(f"✅ Initialized {len(self.protocol_handlers)} protocol handlers")
            for protocol in self.protocol_handlers.keys():
                logger.info(f"   - {protocol.value}: {self.protocol_handlers[protocol].__class__.__name__}")
        except ImportError as ie:
            logger.error(f"❌ Import error when loading handlers: {ie}")
            import traceback
            traceback.print_exc()
            # Initialize empty dict so app doesn't crash
            self.protocol_handlers = {}
        except Exception as e:
            logger.error(f"❌ Error initializing handlers: {e}")
            import traceback
            traceback.print_exc()
            # Initialize empty dict so app doesn't crash
            self.protocol_handlers = {}
    
    async def transfer_file(self, source_path: str, destination_path: str,
                           config: TransferConfig, metadata: Optional[Dict] = None) -> str:
        """Transfer a file"""
        task_id = str(uuid.uuid4())

        task = TransferTask(
            task_id=task_id,
            protocol=config.protocol,
            source_path=source_path,
            destination_path=destination_path,
            config=config,
            status=TransferStatus.PENDING,
            created_at=datetime.utcnow()
        )

        self.monitor.add_transfer(task)

        # Execute transfer immediately - await completion before returning
        # This ensures the transfer completes before the event loop closes
        await self._execute_transfer(task)

        return task_id
    
    async def _execute_transfer(self, task):
        """Execute transfer"""
        try:
            task.status = TransferStatus.IN_PROGRESS
            task.started_at = datetime.utcnow()

            # Update database when transfer starts
            if self.monitor.db:
                try:
                    self.monitor.db.update_file_transfer(
                        task.task_id,
                        status=task.status.value,
                        started_at=task.started_at.isoformat()
                    )
                    logger.debug(f"💾 Transfer {task.task_id} updated in database (started)")
                except Exception as e:
                    logger.error(f"Failed to update transfer start in database: {e}")

            # Log transfer started to activity log
            if activity_logger:
                activity_logger.log_transfer_started(
                    task.task_id,
                    task.source_path,
                    task.destination_path,
                    task.protocol.value
                )

            logger.info(f"🔍 Looking for handler for protocol: {task.protocol}")
            logger.info(f"   Available handlers: {list(self.protocol_handlers.keys())}")

            handler = self.protocol_handlers.get(task.protocol)
            if not handler:
                logger.error(f"❌ No handler found for {task.protocol}")
                logger.error(f"   task.protocol type: {type(task.protocol)}")
                logger.error(f"   task.protocol value: {task.protocol.value if hasattr(task.protocol, 'value') else 'N/A'}")
                logger.error(f"   Available handler keys and types:")
                for key in self.protocol_handlers.keys():
                    logger.error(f"      {key} (type: {type(key)}, value: {key.value if hasattr(key, 'value') else 'N/A'})")
                raise ValueError(f"No handler for {task.protocol}")

            result = await handler.transfer(
                task.source_path,
                task.destination_path,
                task.config
            )

            task.file_size = result.get('file_size')
            task.checksum_md5 = result.get('checksum_md5')
            task.checksum_sha256 = result.get('checksum_sha256')

            self.monitor.complete_transfer(task.task_id)

            # Log transfer completed to activity log
            if activity_logger:
                activity_logger.log_transfer_completed(
                    task.task_id,
                    task.source_path,
                    task.file_size
                )

        except Exception as e:
            self.monitor.fail_transfer(task.task_id, str(e))

            # Log transfer failed to activity log
            if activity_logger:
                activity_logger.log_transfer_failed(
                    task.task_id,
                    task.source_path,
                    str(e)
                )
    
    def get_transfer_status(self, task_id: str) -> Optional[Dict]:
        """Get transfer status"""
        if task_id in self.monitor.active_transfers:
            return self.monitor.active_transfers[task_id].to_dict()
        
        for task in self.monitor.completed_transfers:
            if task.task_id == task_id:
                return task.to_dict()
        
        for task in self.monitor.failed_transfers:
            if task.task_id == task_id:
                return task.to_dict()
        
        return None
    
    def get_statistics(self) -> Dict:
        """Get statistics"""
        return self.monitor.get_statistics()


@dataclass
class TransferTask:
    """Transfer task"""
    task_id: str
    protocol: TransferProtocol
    source_path: str
    destination_path: str
    config: TransferConfig
    status: TransferStatus = TransferStatus.PENDING
    created_at: datetime = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    file_size: Optional[int] = None
    transferred_bytes: int = 0
    checksum_md5: Optional[str] = None
    checksum_sha256: Optional[str] = None
    error_message: Optional[str] = None
    retry_attempts: int = 0
    metadata: Dict = None
    
    def to_dict(self) -> Dict:
        return {
            'task_id': self.task_id,
            'protocol': self.protocol.value,
            'source_path': self.source_path,
            'destination_path': self.destination_path,
            'status': self.status.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'file_size': self.file_size,
            'transferred_bytes': self.transferred_bytes,
            'checksum_md5': self.checksum_md5,
            'checksum_sha256': self.checksum_sha256,
            'error_message': self.error_message,
            'retry_attempts': self.retry_attempts
        }


class TransferMonitor:
    """Monitor transfers"""

    def __init__(self, state_manager=None):
        self.active_transfers: Dict[str, TransferTask] = {}
        self.completed_transfers = []
        self.failed_transfers = []
        self.state_manager = state_manager

        # Initialize database connection
        self.db = None
        if get_database:
            try:
                self.db = get_database()
                logger.info("✅ Database initialized for transfer tracking")
            except Exception as e:
                logger.error(f"❌ Failed to initialize database: {e}")
                self.db = None

    def add_transfer(self, task: TransferTask):
        self.active_transfers[task.task_id] = task
        logger.info(f"Transfer {task.task_id} added")

        # Save to database
        if self.db and FileTransfer:
            try:
                transfer = FileTransfer(
                    task_id=task.task_id,
                    source_path=task.source_path,
                    destination_path=task.destination_path,
                    protocol=task.protocol.value,
                    status=task.status.value,
                    file_size=task.file_size,
                    transferred_bytes=task.transferred_bytes,
                    checksum_md5=task.checksum_md5,
                    checksum_sha256=task.checksum_sha256,
                    error_message=task.error_message,
                    retry_attempts=task.retry_attempts,
                    created_at=task.created_at.isoformat() if task.created_at else None,
                    started_at=task.started_at.isoformat() if task.started_at else None,
                    completed_at=task.completed_at.isoformat() if task.completed_at else None,
                    metadata=json.dumps(task.metadata or {})
                )
                self.db.create_file_transfer(transfer)
                logger.debug(f"💾 Transfer {task.task_id} saved to database")
            except Exception as e:
                logger.error(f"Failed to save transfer to database: {e}")

        self._save_state()

    def complete_transfer(self, task_id: str):
        if task_id in self.active_transfers:
            task = self.active_transfers.pop(task_id)
            task.status = TransferStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            self.completed_transfers.append(task)
            logger.info(f"Transfer {task_id} completed")

            # Update database
            if self.db:
                try:
                    self.db.update_file_transfer(
                        task_id,
                        status=task.status.value,
                        completed_at=task.completed_at.isoformat(),
                        file_size=task.file_size,
                        checksum_md5=task.checksum_md5,
                        checksum_sha256=task.checksum_sha256
                    )
                    logger.debug(f"💾 Transfer {task_id} updated in database (completed)")
                except Exception as e:
                    logger.error(f"Failed to update transfer in database: {e}")

            self._save_state()

    def fail_transfer(self, task_id: str, error: str):
        if task_id in self.active_transfers:
            task = self.active_transfers.pop(task_id)
            task.status = TransferStatus.FAILED
            task.error_message = error
            task.completed_at = datetime.utcnow()
            self.failed_transfers.append(task)
            logger.error(f"Transfer {task_id} failed: {error}")

            # Update database
            if self.db:
                try:
                    self.db.update_file_transfer(
                        task_id,
                        status=task.status.value,
                        error_message=error,
                        completed_at=task.completed_at.isoformat()
                    )
                    logger.debug(f"💾 Transfer {task_id} updated in database (failed)")
                except Exception as e:
                    logger.error(f"Failed to update transfer in database: {e}")

            self._save_state()

    def _save_state(self):
        """Save transfer state to disk"""
        if self.state_manager:
            try:
                active = [t.to_dict() for t in self.active_transfers.values()]
                completed = [t.to_dict() for t in self.completed_transfers]
                failed = [t.to_dict() for t in self.failed_transfers]
                self.state_manager.save_transfers(active, completed, failed)
            except Exception as e:
                logger.error(f"Failed to save transfer state: {e}")

    def load_state(self):
        """Load transfer state from database and disk"""
        # First, try to load from database (primary source)
        if self.db:
            try:
                logger.info("Loading transfer history from database...")

                # Load completed transfers from database
                completed_from_db = self.db.get_file_transfers(status='completed', limit=1000)
                for db_transfer in completed_from_db:
                    task = self._db_transfer_to_task(db_transfer)
                    if task:
                        self.completed_transfers.append(task)

                # Load failed transfers from database
                failed_from_db = self.db.get_file_transfers(status='failed', limit=1000)
                for db_transfer in failed_from_db:
                    task = self._db_transfer_to_task(db_transfer)
                    if task:
                        self.failed_transfers.append(task)

                logger.info(f"📋 Loaded from database: {len(self.completed_transfers)} completed and {len(self.failed_transfers)} failed transfers")
            except Exception as e:
                logger.error(f"Failed to load transfer state from database: {e}")

        # Fallback: Load from JSON if database didn't load anything
        if len(self.completed_transfers) == 0 and len(self.failed_transfers) == 0:
            if self.state_manager:
                try:
                    logger.info("Loading transfer history from JSON (fallback)...")
                    data = self.state_manager.load_transfers()
                    if data:
                        # Restore completed transfers
                        for transfer_dict in data.get('completed_transfers', []):
                            task = self._dict_to_task(transfer_dict)
                            if task:
                                self.completed_transfers.append(task)

                        # Restore failed transfers
                        for transfer_dict in data.get('failed_transfers', []):
                            task = self._dict_to_task(transfer_dict)
                            if task:
                                self.failed_transfers.append(task)

                        logger.info(f"📋 Loaded from JSON: {len(self.completed_transfers)} completed and {len(self.failed_transfers)} failed transfers")
                except Exception as e:
                    logger.error(f"Failed to load transfer state from JSON: {e}")

    def _db_transfer_to_task(self, db_transfer) -> Optional[TransferTask]:
        """Convert database FileTransfer to TransferTask"""
        try:
            # Create a minimal TransferConfig (won't be used for completed transfers)
            config = TransferConfig(
                protocol=TransferProtocol(db_transfer.protocol),
                host="localhost",
                port=22
            )

            task = TransferTask(
                task_id=db_transfer.task_id,
                source_path=db_transfer.source_path,
                destination_path=db_transfer.destination_path,
                protocol=TransferProtocol(db_transfer.protocol),
                config=config,
                file_size=db_transfer.file_size
            )
            task.status = TransferStatus(db_transfer.status)
            task.error_message = db_transfer.error_message
            task.checksum_md5 = db_transfer.checksum_md5
            task.checksum_sha256 = db_transfer.checksum_sha256
            task.retry_attempts = db_transfer.retry_attempts

            # Parse timestamps
            if db_transfer.created_at:
                task.created_at = datetime.fromisoformat(db_transfer.created_at)
            if db_transfer.started_at:
                task.started_at = datetime.fromisoformat(db_transfer.started_at)
            if db_transfer.completed_at:
                task.completed_at = datetime.fromisoformat(db_transfer.completed_at)

            return task
        except Exception as e:
            logger.error(f"Failed to convert database transfer to task: {e}")
            return None

    def _dict_to_task(self, d: dict) -> Optional[TransferTask]:
        """Convert dictionary to TransferTask"""
        try:
            task = TransferTask(
                task_id=d['task_id'],
                source_path=d['source_path'],
                destination_path=d['destination_path'],
                protocol=TransferProtocol(d['protocol']),
                file_size=d.get('file_size', 0)
            )
            task.status = TransferStatus(d['status'])
            task.error_message = d.get('error_message')
            if d.get('created_at'):
                task.created_at = datetime.fromisoformat(d['created_at'])
            if d.get('completed_at'):
                task.completed_at = datetime.fromisoformat(d['completed_at'])
            return task
        except Exception as e:
            logger.error(f"Failed to restore transfer task: {e}")
            return None
    
    def get_statistics(self) -> Dict:
        total = len(self.active_transfers) + len(self.completed_transfers) + len(self.failed_transfers)
        return {
            'active_transfers': len(self.active_transfers),
            'completed_transfers': len(self.completed_transfers),
            'failed_transfers': len(self.failed_transfers),
            'total_transfers': total,
            'success_rate': (len(self.completed_transfers) / total * 100) if total > 0 else 0
        }
    
    def get_active_transfers(self) -> list:
        return [t.to_dict() for t in self.active_transfers.values()]
