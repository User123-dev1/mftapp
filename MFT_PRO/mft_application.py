"""
Simplified MFT Application Core
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)


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
    
    def __init__(self):
        self.monitor = TransferMonitor()
        self.protocol_handlers = {}
        self._initialize_handlers()
        logger.info("MFT Application initialized")
    
    def _initialize_handlers(self):
        """Initialize protocol handlers"""
        try:
            from protocol_handlers import (
                SFTPHandler, FTPSHandler, HTTPSHandler,
                WebDAVHandler, SMBHandler, UNCHandler,
                TFTPHandler, AS2Handler
            )

            logger.info("🔧 Initializing protocol handlers...")

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
            
            handler = self.protocol_handlers.get(task.protocol)
            if not handler:
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
            
        except Exception as e:
            self.monitor.fail_transfer(task.task_id, str(e))
    
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
    
    def __init__(self):
        self.active_transfers: Dict[str, TransferTask] = {}
        self.completed_transfers = []
        self.failed_transfers = []
    
    def add_transfer(self, task: TransferTask):
        self.active_transfers[task.task_id] = task
        logger.info(f"Transfer {task.task_id} added")
    
    def complete_transfer(self, task_id: str):
        if task_id in self.active_transfers:
            task = self.active_transfers.pop(task_id)
            task.status = TransferStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            self.completed_transfers.append(task)
            logger.info(f"Transfer {task_id} completed")
    
    def fail_transfer(self, task_id: str, error: str):
        if task_id in self.active_transfers:
            task = self.active_transfers.pop(task_id)
            task.status = TransferStatus.FAILED
            task.error_message = error
            task.completed_at = datetime.utcnow()
            self.failed_transfers.append(task)
            logger.error(f"Transfer {task_id} failed: {error}")
    
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
