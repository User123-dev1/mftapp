"""
Test Suite for MFT Application
Run with: pytest test_mft.py -v
"""

import pytest
import asyncio
from datetime import datetime
import uuid

from mft_application import (
    MFTApplication, TransferConfig, TransferProtocol,
    ComplianceFramework, TransferStatus, ComplianceManager
)
from monitoring_system import (
    MetricsCollector, AlertManager, AlertRule, 
    AlertSeverity, MonitoringDashboard
)
from scheduler_system import (
    TransferScheduler, ScheduledTransfer, ScheduleType,
    ScheduleStatus
)


class TestMFTApplication:
    """Test MFT Application core functionality"""
    
    @pytest.fixture
    def mft_app(self):
        """Create MFT application instance"""
        return MFTApplication()
    
    @pytest.fixture
    def sftp_config(self):
        """Create SFTP configuration"""
        return TransferConfig(
            protocol=TransferProtocol.SFTP,
            host="sftp.example.com",
            port=22,
            username="testuser",
            encryption_enabled=True
        )
    
    def test_application_initialization(self, mft_app):
        """Test application initializes correctly"""
        assert mft_app is not None
        assert mft_app.compliance_manager is not None
        assert mft_app.monitor is not None
    
    def test_transfer_config_creation(self, sftp_config):
        """Test transfer configuration"""
        assert sftp_config.protocol == TransferProtocol.SFTP
        assert sftp_config.encryption_enabled is True
        assert sftp_config.port == 22
    
    def test_compliance_validation_hipaa(self):
        """Test HIPAA compliance validation"""
        compliance_manager = ComplianceManager()
        
        from mft_application import TransferTask
        
        # Valid HIPAA transfer
        valid_task = TransferTask(
            task_id=str(uuid.uuid4()),
            protocol=TransferProtocol.SFTP,
            source_path="/test/file.dat",
            destination_path="/remote/file.dat",
            config=TransferConfig(
                protocol=TransferProtocol.SFTP,
                host="test.com",
                port=22,
                encryption_enabled=True
            ),
            checksum_sha256="abc123"
        )
        
        result = compliance_manager._validate_hipaa(valid_task)
        assert result is True
        
        # Invalid HIPAA transfer (no encryption)
        invalid_task = TransferTask(
            task_id=str(uuid.uuid4()),
            protocol=TransferProtocol.FTP,
            source_path="/test/file.dat",
            destination_path="/remote/file.dat",
            config=TransferConfig(
                protocol=TransferProtocol.FTP,
                host="test.com",
                port=21,
                encryption_enabled=False
            )
        )
        
        result = compliance_manager._validate_hipaa(invalid_task)
        assert result is False
    
    def test_monitor_statistics(self, mft_app):
        """Test monitoring statistics"""
        stats = mft_app.get_statistics()
        
        assert 'active_transfers' in stats
        assert 'completed_transfers' in stats
        assert 'failed_transfers' in stats
        assert 'total_transfers' in stats
        assert 'success_rate' in stats


class TestMonitoringSystem:
    """Test monitoring and alerting system"""
    
    @pytest.fixture
    async def metrics_collector(self):
        """Create metrics collector"""
        collector = MetricsCollector(retention_hours=1)
        await collector.start()
        yield collector
        await collector.stop()
    
    @pytest.fixture
    async def alert_manager(self, metrics_collector):
        """Create alert manager"""
        manager = AlertManager(metrics_collector)
        await manager.start()
        yield manager
        await manager.stop()
    
    @pytest.mark.asyncio
    async def test_metrics_collection(self, metrics_collector):
        """Test metrics collection"""
        # Record some metrics
        metrics_collector.record_metric("test_metric", 100.0)
        metrics_collector.record_metric("test_metric", 150.0)
        
        # Get metrics
        metrics = metrics_collector.get_metrics("test_metric")
        assert len(metrics) == 2
        
        # Get latest value
        latest = metrics_collector.get_latest_value("test_metric")
        assert latest == 150.0
        
        # Get average
        avg = metrics_collector.get_average("test_metric", minutes=1)
        assert avg == 125.0
    
    @pytest.mark.asyncio
    async def test_alert_rule_creation(self, alert_manager):
        """Test alert rule creation"""
        rule = AlertRule(
            rule_id="test_rule",
            metric_name="error_rate",
            condition=">",
            threshold=5.0,
            severity=AlertSeverity.ERROR
        )
        
        alert_manager.add_alert_rule(rule)
        
        assert "test_rule" in alert_manager.alert_rules
        assert alert_manager.alert_rules["test_rule"].threshold == 5.0
    
    @pytest.mark.asyncio
    async def test_dashboard_data(self, metrics_collector, alert_manager):
        """Test monitoring dashboard"""
        # Record some metrics
        metrics_collector.record_metric("transfer_count", 10)
        metrics_collector.record_metric("error_rate", 2.0)
        
        dashboard = MonitoringDashboard(metrics_collector, alert_manager)
        data = dashboard.get_dashboard_data(time_range_minutes=5)
        
        assert 'timestamp' in data
        assert 'metrics' in data
        assert 'active_alerts' in data
        assert 'system_health' in data


class TestSchedulerSystem:
    """Test scheduler system"""
    
    @pytest.fixture
    def scheduler(self):
        """Create scheduler instance"""
        mft_app = MFTApplication()
        return TransferScheduler(mft_app)
    
    @pytest.fixture
    def recurring_schedule(self):
        """Create recurring schedule"""
        return ScheduledTransfer(
            schedule_id=str(uuid.uuid4()),
            name="Test Recurring",
            description="Test recurring transfer",
            schedule_type=ScheduleType.RECURRING,
            source_path="/test/source",
            destination_path="/test/dest",
            config=TransferConfig(
                protocol=TransferProtocol.SFTP,
                host="test.com",
                port=22,
                encryption_enabled=True
            ),
            interval_minutes=60
        )
    
    @pytest.fixture
    def cron_schedule(self):
        """Create cron schedule"""
        return ScheduledTransfer(
            schedule_id=str(uuid.uuid4()),
            name="Test Cron",
            description="Test cron transfer",
            schedule_type=ScheduleType.CRON,
            source_path="/test/source",
            destination_path="/test/dest",
            config=TransferConfig(
                protocol=TransferProtocol.SFTP,
                host="test.com",
                port=22,
                encryption_enabled=True
            ),
            cron_expression="0 2 * * *"  # Daily at 2 AM
        )
    
    def test_add_recurring_schedule(self, scheduler, recurring_schedule):
        """Test adding recurring schedule"""
        schedule_id = scheduler.add_schedule(recurring_schedule)
        
        assert schedule_id in scheduler.schedules
        assert scheduler.schedules[schedule_id].status == ScheduleStatus.ACTIVE
        assert scheduler.schedules[schedule_id].next_execution is not None
    
    def test_add_cron_schedule(self, scheduler, cron_schedule):
        """Test adding cron schedule"""
        schedule_id = scheduler.add_schedule(cron_schedule)
        
        assert schedule_id in scheduler.schedules
        assert scheduler.schedules[schedule_id].cron_expression == "0 2 * * *"
    
    def test_pause_resume_schedule(self, scheduler, recurring_schedule):
        """Test pausing and resuming schedule"""
        schedule_id = scheduler.add_schedule(recurring_schedule)
        
        # Pause
        scheduler.pause_schedule(schedule_id)
        assert scheduler.schedules[schedule_id].status == ScheduleStatus.PAUSED
        
        # Resume
        scheduler.resume_schedule(schedule_id)
        assert scheduler.schedules[schedule_id].status == ScheduleStatus.ACTIVE
    
    def test_list_schedules(self, scheduler, recurring_schedule, cron_schedule):
        """Test listing schedules"""
        scheduler.add_schedule(recurring_schedule)
        scheduler.add_schedule(cron_schedule)
        
        all_schedules = scheduler.list_schedules()
        assert len(all_schedules) == 2
        
        # Filter by status
        active_schedules = scheduler.list_schedules(status=ScheduleStatus.ACTIVE)
        assert len(active_schedules) == 2


class TestCompliance:
    """Test compliance frameworks"""
    
    @pytest.fixture
    def compliance_manager(self):
        """Create compliance manager"""
        return ComplianceManager()
    
    def test_gdpr_validation(self, compliance_manager):
        """Test GDPR compliance validation"""
        from mft_application import TransferTask
        
        task = TransferTask(
            task_id=str(uuid.uuid4()),
            protocol=TransferProtocol.SFTP,
            source_path="/test/file.dat",
            destination_path="/remote/file.dat",
            config=TransferConfig(
                protocol=TransferProtocol.SFTP,
                host="test.com",
                port=22,
                encryption_enabled=True
            ),
            metadata={'data_subject_consent': True}
        )
        
        result = compliance_manager._validate_gdpr(task)
        assert result is True
    
    def test_pci_dss_validation(self, compliance_manager):
        """Test PCI DSS compliance validation"""
        from mft_application import TransferTask
        
        task = TransferTask(
            task_id=str(uuid.uuid4()),
            protocol=TransferProtocol.HTTPS,
            source_path="/test/file.dat",
            destination_path="/remote/file.dat",
            config=TransferConfig(
                protocol=TransferProtocol.HTTPS,
                host="test.com",
                port=443,
                encryption_enabled=True,
                verify_ssl=True
            )
        )
        
        result = compliance_manager._validate_pci_dss(task)
        assert result is True


class TestProtocolSupport:
    """Test protocol support"""
    
    def test_supported_protocols(self):
        """Test all supported protocols are defined"""
        protocols = [p.value for p in TransferProtocol]
        
        expected = ['sftp', 'as2', 'https', 'http', 'ftps', 'ftp', 
                   'oftp2', 'aftp', 'tftp', 'webdav', 'smb', 'unc']
        
        for protocol in expected:
            assert protocol in protocols
    
    def test_supported_compliance_frameworks(self):
        """Test all compliance frameworks are defined"""
        frameworks = [f.value for f in ComplianceFramework]
        
        expected = ['hipaa', 'gdpr', 'pci_dss', 'glba', 
                   'iso_27001', 'soc2_type_ii', 'sox']
        
        for framework in expected:
            assert framework in frameworks


# Integration tests
class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_full_monitoring_flow(self):
        """Test complete monitoring flow"""
        # Initialize components
        metrics = MetricsCollector(retention_hours=1)
        alerts = AlertManager(metrics)
        
        await metrics.start()
        await alerts.start()
        
        try:
            # Add alert rule
            rule = AlertRule(
                rule_id="test_high_error",
                metric_name="error_count",
                condition=">",
                threshold=10.0,
                severity=AlertSeverity.ERROR
            )
            alerts.add_alert_rule(rule)
            
            # Record metrics
            for i in range(15):
                metrics.record_metric("error_count", float(i))
                await asyncio.sleep(0.1)
            
            # Check metrics were recorded
            assert metrics.get_latest_value("error_count") == 14.0
            
        finally:
            await alerts.stop()
            await metrics.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
