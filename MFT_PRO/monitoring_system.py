"""
Advanced Monitoring and Alerting System for MFT Application
Provides real-time monitoring, metrics, alerts, and dashboards
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MetricType(Enum):
    """Types of metrics to track"""
    TRANSFER_COUNT = "transfer_count"
    TRANSFER_VOLUME = "transfer_volume"
    SUCCESS_RATE = "success_rate"
    AVERAGE_DURATION = "average_duration"
    ERROR_RATE = "error_rate"
    ACTIVE_CONNECTIONS = "active_connections"
    BANDWIDTH_USAGE = "bandwidth_usage"
    QUEUE_SIZE = "queue_size"


@dataclass
class Alert:
    """Alert definition"""
    alert_id: str
    severity: AlertSeverity
    title: str
    message: str
    timestamp: datetime
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    acknowledged: bool = False
    resolved: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'alert_id': self.alert_id,
            'severity': self.severity.value,
            'title': self.title,
            'message': self.message,
            'timestamp': self.timestamp.isoformat(),
            'metric_name': self.metric_name,
            'metric_value': self.metric_value,
            'threshold': self.threshold,
            'acknowledged': self.acknowledged,
            'resolved': self.resolved
        }


@dataclass
class Metric:
    """Metric data point"""
    name: str
    value: float
    timestamp: datetime
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'value': self.value,
            'timestamp': self.timestamp.isoformat(),
            'tags': self.tags
        }


@dataclass
class AlertRule:
    """Alert rule configuration"""
    rule_id: str
    metric_name: str
    condition: str  # ">", "<", ">=", "<=", "=="
    threshold: float
    severity: AlertSeverity
    duration: int = 0  # Seconds the condition must persist
    enabled: bool = True
    notification_channels: List[str] = field(default_factory=list)


class MetricsCollector:
    """Collects and stores metrics"""
    
    def __init__(self, retention_hours: int = 24):
        self.metrics: Dict[str, List[Metric]] = {}
        self.retention_hours = retention_hours
        self._cleanup_task = None
        
    async def start(self):
        """Start the metrics collector"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
    async def stop(self):
        """Stop the metrics collector"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
    
    def record_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a metric"""
        metric = Metric(
            name=name,
            value=value,
            timestamp=datetime.utcnow(),
            tags=tags or {}
        )
        
        if name not in self.metrics:
            self.metrics[name] = []
        
        self.metrics[name].append(metric)
        logger.debug(f"Recorded metric: {name}={value}")
    
    def get_metrics(self, 
                   name: str, 
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None) -> List[Metric]:
        """Get metrics for a specific name within time range"""
        if name not in self.metrics:
            return []
        
        metrics = self.metrics[name]
        
        if start_time:
            metrics = [m for m in metrics if m.timestamp >= start_time]
        
        if end_time:
            metrics = [m for m in metrics if m.timestamp <= end_time]
        
        return metrics
    
    def get_latest_value(self, name: str) -> Optional[float]:
        """Get the latest value for a metric"""
        if name not in self.metrics or not self.metrics[name]:
            return None
        
        return self.metrics[name][-1].value
    
    def get_average(self, name: str, minutes: int = 5) -> Optional[float]:
        """Get average value over the last N minutes"""
        start_time = datetime.utcnow() - timedelta(minutes=minutes)
        metrics = self.get_metrics(name, start_time=start_time)
        
        if not metrics:
            return None
        
        return sum(m.value for m in metrics) / len(metrics)
    
    async def _cleanup_loop(self):
        """Periodically clean up old metrics"""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                self._cleanup_old_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics cleanup: {e}")
    
    def _cleanup_old_metrics(self):
        """Remove metrics older than retention period"""
        cutoff_time = datetime.utcnow() - timedelta(hours=self.retention_hours)
        
        for name in self.metrics:
            self.metrics[name] = [
                m for m in self.metrics[name] 
                if m.timestamp > cutoff_time
            ]
        
        logger.info("Metrics cleanup completed")


class AlertManager:
    """Manages alerts and notifications"""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
        self.alert_rules: Dict[str, AlertRule] = {}
        self.active_alerts: Dict[str, Alert] = {}
        self.alert_history: List[Alert] = []
        self.notification_handlers: Dict[str, Callable] = {}
        self._monitoring_task = None
        
    async def start(self):
        """Start the alert manager"""
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
        
    async def stop(self):
        """Stop the alert manager"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
    
    def add_alert_rule(self, rule: AlertRule):
        """Add an alert rule"""
        self.alert_rules[rule.rule_id] = rule
        logger.info(f"Added alert rule: {rule.rule_id}")
    
    def remove_alert_rule(self, rule_id: str):
        """Remove an alert rule"""
        if rule_id in self.alert_rules:
            del self.alert_rules[rule_id]
            logger.info(f"Removed alert rule: {rule_id}")
    
    def register_notification_handler(self, channel: str, handler: Callable):
        """Register a notification handler"""
        self.notification_handlers[channel] = handler
        logger.info(f"Registered notification handler: {channel}")
    
    async def _monitoring_loop(self):
        """Continuously monitor metrics against alert rules"""
        while True:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                await self._check_alert_rules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in alert monitoring: {e}")
    
    async def _check_alert_rules(self):
        """Check all alert rules"""
        for rule_id, rule in self.alert_rules.items():
            if not rule.enabled:
                continue
            
            # Get current metric value
            current_value = self.metrics_collector.get_latest_value(rule.metric_name)
            
            if current_value is None:
                continue
            
            # Evaluate condition
            triggered = self._evaluate_condition(
                current_value, 
                rule.condition, 
                rule.threshold
            )
            
            if triggered:
                # Create or update alert
                await self._trigger_alert(rule, current_value)
            else:
                # Resolve alert if it exists
                if rule_id in self.active_alerts:
                    await self._resolve_alert(rule_id)
    
    def _evaluate_condition(self, value: float, condition: str, threshold: float) -> bool:
        """Evaluate alert condition"""
        if condition == ">":
            return value > threshold
        elif condition == "<":
            return value < threshold
        elif condition == ">=":
            return value >= threshold
        elif condition == "<=":
            return value <= threshold
        elif condition == "==":
            return value == threshold
        else:
            logger.warning(f"Unknown condition: {condition}")
            return False
    
    async def _trigger_alert(self, rule: AlertRule, current_value: float):
        """Trigger an alert"""
        # Check if alert already exists
        if rule.rule_id in self.active_alerts:
            return
        
        # Create new alert
        alert = Alert(
            alert_id=f"alert_{rule.rule_id}_{datetime.utcnow().timestamp()}",
            severity=rule.severity,
            title=f"{rule.metric_name} Alert",
            message=f"{rule.metric_name} is {current_value} (threshold: {rule.threshold})",
            timestamp=datetime.utcnow(),
            metric_name=rule.metric_name,
            metric_value=current_value,
            threshold=rule.threshold
        )
        
        self.active_alerts[rule.rule_id] = alert
        self.alert_history.append(alert)
        
        logger.warning(f"Alert triggered: {alert.title}")
        
        # Send notifications
        await self._send_notifications(alert, rule.notification_channels)
    
    async def _resolve_alert(self, rule_id: str):
        """Resolve an alert"""
        if rule_id not in self.active_alerts:
            return
        
        alert = self.active_alerts[rule_id]
        alert.resolved = True
        
        del self.active_alerts[rule_id]
        
        logger.info(f"Alert resolved: {alert.title}")
    
    async def _send_notifications(self, alert: Alert, channels: List[str]):
        """Send alert notifications"""
        for channel in channels:
            if channel in self.notification_handlers:
                try:
                    await self.notification_handlers[channel](alert)
                except Exception as e:
                    logger.error(f"Error sending notification to {channel}: {e}")
    
    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get all active alerts"""
        return [alert.to_dict() for alert in self.active_alerts.values()]
    
    def acknowledge_alert(self, rule_id: str):
        """Acknowledge an alert"""
        if rule_id in self.active_alerts:
            self.active_alerts[rule_id].acknowledged = True
            logger.info(f"Alert acknowledged: {rule_id}")


class NotificationService:
    """Handles various notification channels"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def send_email(self, alert: Alert):
        """Send email notification"""
        if 'email' not in self.config:
            logger.warning("Email configuration not found")
            return
        
        email_config = self.config['email']
        
        msg = MIMEMultipart()
        msg['From'] = email_config['from']
        msg['To'] = ', '.join(email_config['to'])
        msg['Subject'] = f"[{alert.severity.value.upper()}] {alert.title}"
        
        body = f"""
        Alert Details:
        --------------
        Severity: {alert.severity.value.upper()}
        Title: {alert.title}
        Message: {alert.message}
        Timestamp: {alert.timestamp.isoformat()}
        
        Metric: {alert.metric_name}
        Current Value: {alert.metric_value}
        Threshold: {alert.threshold}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        try:
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._send_smtp_email,
                email_config,
                msg
            )
            logger.info(f"Email notification sent for alert: {alert.alert_id}")
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
    
    def _send_smtp_email(self, config: Dict[str, Any], msg: MIMEMultipart):
        """Send email via SMTP"""
        with smtplib.SMTP(config['smtp_host'], config['smtp_port']) as server:
            if config.get('use_tls', True):
                server.starttls()
            if config.get('username') and config.get('password'):
                server.login(config['username'], config['password'])
            server.send_message(msg)
    
    async def send_webhook(self, alert: Alert):
        """Send webhook notification"""
        if 'webhook' not in self.config:
            logger.warning("Webhook configuration not found")
            return
        
        webhook_config = self.config['webhook']
        
        payload = {
            'alert_id': alert.alert_id,
            'severity': alert.severity.value,
            'title': alert.title,
            'message': alert.message,
            'timestamp': alert.timestamp.isoformat(),
            'metric_name': alert.metric_name,
            'metric_value': alert.metric_value,
            'threshold': alert.threshold
        }
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_config['url'],
                    json=payload,
                    headers=webhook_config.get('headers', {})
                ) as response:
                    response.raise_for_status()
                    logger.info(f"Webhook notification sent for alert: {alert.alert_id}")
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
    
    async def send_slack(self, alert: Alert):
        """Send Slack notification"""
        if 'slack' not in self.config:
            logger.warning("Slack configuration not found")
            return
        
        slack_config = self.config['slack']
        
        # Color based on severity
        colors = {
            AlertSeverity.INFO: '#36a64f',
            AlertSeverity.WARNING: '#ff9900',
            AlertSeverity.ERROR: '#ff0000',
            AlertSeverity.CRITICAL: '#8b0000'
        }
        
        payload = {
            'attachments': [{
                'color': colors.get(alert.severity, '#cccccc'),
                'title': alert.title,
                'text': alert.message,
                'fields': [
                    {'title': 'Severity', 'value': alert.severity.value.upper(), 'short': True},
                    {'title': 'Metric', 'value': alert.metric_name or 'N/A', 'short': True},
                    {'title': 'Current Value', 'value': str(alert.metric_value), 'short': True},
                    {'title': 'Threshold', 'value': str(alert.threshold), 'short': True},
                ],
                'timestamp': int(alert.timestamp.timestamp())
            }]
        }
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    slack_config['webhook_url'],
                    json=payload
                ) as response:
                    response.raise_for_status()
                    logger.info(f"Slack notification sent for alert: {alert.alert_id}")
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")


class MonitoringDashboard:
    """Provides monitoring dashboard data"""
    
    def __init__(self, 
                 metrics_collector: MetricsCollector,
                 alert_manager: AlertManager):
        self.metrics_collector = metrics_collector
        self.alert_manager = alert_manager
    
    def get_dashboard_data(self, time_range_minutes: int = 60) -> Dict[str, Any]:
        """Get comprehensive dashboard data"""
        start_time = datetime.utcnow() - timedelta(minutes=time_range_minutes)
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'time_range_minutes': time_range_minutes,
            'metrics': self._get_metric_summaries(start_time),
            'active_alerts': self.alert_manager.get_active_alerts(),
            'system_health': self._get_system_health()
        }
    
    def _get_metric_summaries(self, start_time: datetime) -> Dict[str, Any]:
        """Get summaries for all metrics"""
        summaries = {}
        
        for metric_name in self.metrics_collector.metrics.keys():
            metrics = self.metrics_collector.get_metrics(metric_name, start_time=start_time)
            
            if metrics:
                values = [m.value for m in metrics]
                summaries[metric_name] = {
                    'current': values[-1],
                    'min': min(values),
                    'max': max(values),
                    'avg': sum(values) / len(values),
                    'count': len(values)
                }
        
        return summaries
    
    def _get_system_health(self) -> Dict[str, str]:
        """Get overall system health status"""
        active_alerts = self.alert_manager.get_active_alerts()
        
        critical_count = sum(1 for a in active_alerts if a['severity'] == 'critical')
        error_count = sum(1 for a in active_alerts if a['severity'] == 'error')
        
        if critical_count > 0:
            return {'status': 'critical', 'message': f'{critical_count} critical alerts'}
        elif error_count > 0:
            return {'status': 'degraded', 'message': f'{error_count} error alerts'}
        elif len(active_alerts) > 0:
            return {'status': 'warning', 'message': f'{len(active_alerts)} active alerts'}
        else:
            return {'status': 'healthy', 'message': 'All systems operational'}


# Example usage
if __name__ == "__main__":
    async def main():
        # Initialize components
        metrics = MetricsCollector(retention_hours=24)
        alerts = AlertManager(metrics)
        
        await metrics.start()
        await alerts.start()
        
        # Add some alert rules
        alerts.add_alert_rule(AlertRule(
            rule_id="high_error_rate",
            metric_name="error_rate",
            condition=">",
            threshold=5.0,
            severity=AlertSeverity.ERROR,
            notification_channels=["email", "slack"]
        ))
        
        # Setup notifications
        notification_service = NotificationService({
            'email': {
                'from': 'mft@example.com',
                'to': ['admin@example.com'],
                'smtp_host': 'smtp.example.com',
                'smtp_port': 587,
                'use_tls': True
            }
        })
        
        alerts.register_notification_handler('email', notification_service.send_email)
        
        # Simulate some metrics
        for i in range(100):
            metrics.record_metric("transfer_count", i)
            metrics.record_metric("error_rate", i * 0.1)
            await asyncio.sleep(1)
        
        # Get dashboard data
        dashboard = MonitoringDashboard(metrics, alerts)
        data = dashboard.get_dashboard_data(time_range_minutes=5)
        print(json.dumps(data, indent=2))
        
        await alerts.stop()
        await metrics.stop()
    
    asyncio.run(main())
