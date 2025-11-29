"""
Configuration Management System for MFT Application
Handles loading, validation, and management of application configuration
"""

import os
import yaml
import json
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """Application configuration"""
    name: str = "MFT Application"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"


@dataclass
class APIConfig:
    """API server configuration"""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    reload: bool = False
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class MonitoringConfig:
    """Monitoring configuration"""
    enabled: bool = True
    metrics_retention_hours: int = 24
    check_interval_seconds: int = 10


@dataclass
class EmailConfig:
    """Email notification configuration"""
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True
    from_address: str = ""
    to_addresses: List[str] = field(default_factory=list)
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class SlackConfig:
    """Slack notification configuration"""
    enabled: bool = False
    webhook_url: Optional[str] = None


@dataclass
class WebhookConfig:
    """Webhook notification configuration"""
    enabled: bool = False
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class AlertConfig:
    """Alert configuration"""
    enabled: bool = True
    email: EmailConfig = field(default_factory=EmailConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


@dataclass
class ComplianceConfig:
    """Compliance configuration"""
    audit_log_path: str = "/var/log/mft/audit.log"
    required_frameworks: List[str] = field(default_factory=list)
    encryption_required: bool = True
    checksum_verification: bool = True


@dataclass
class SchedulerConfig:
    """Scheduler configuration"""
    enabled: bool = True
    execution_history_limit: int = 1000
    max_concurrent_executions: int = 10


@dataclass
class DatabaseConfig:
    """Database configuration"""
    enabled: bool = False
    type: str = "postgresql"  # postgresql, mysql, sqlite
    host: str = "localhost"
    port: int = 5432
    database: str = "mft"
    username: str = "mft_user"
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20


@dataclass
class RedisConfig:
    """Redis configuration"""
    enabled: bool = False
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    ssl: bool = False


@dataclass
class StorageConfig:
    """Storage configuration"""
    temp_directory: str = "/tmp/mft"
    max_file_size_mb: int = 1024
    cleanup_interval_hours: int = 24


@dataclass
class SecurityConfig:
    """Security configuration"""
    secret_key: str = ""
    token_expiration_hours: int = 24
    max_login_attempts: int = 5
    password_min_length: int = 12
    require_ssl: bool = True


@dataclass
class MFTConfig:
    """Main MFT configuration"""
    app: AppConfig = field(default_factory=AppConfig)
    api: APIConfig = field(default_factory=APIConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    def save(self, filepath: str):
        """Save configuration to file"""
        config_dict = self.to_dict()
        
        # Determine format from extension
        ext = Path(filepath).suffix.lower()
        
        with open(filepath, 'w') as f:
            if ext in ['.yaml', '.yml']:
                yaml.dump(config_dict, f, default_flow_style=False)
            elif ext == '.json':
                json.dump(config_dict, f, indent=2)
            else:
                raise ValueError(f"Unsupported config format: {ext}")
        
        logger.info(f"Configuration saved to {filepath}")


class ConfigurationManager:
    """Manages application configuration"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config: Optional[MFTConfig] = None
        
    def load(self) -> MFTConfig:
        """Load configuration from file or environment"""
        # Load environment variables
        load_dotenv()
        
        # Start with default configuration
        config = MFTConfig()
        
        # Load from file if specified
        if self.config_path and os.path.exists(self.config_path):
            file_config = self._load_from_file(self.config_path)
            config = self._merge_configs(config, file_config)
            logger.info(f"Configuration loaded from {self.config_path}")
        
        # Override with environment variables
        config = self._override_from_env(config)
        
        # Validate configuration
        self._validate_config(config)
        
        self.config = config
        return config
    
    def _load_from_file(self, filepath: str) -> Dict[str, Any]:
        """Load configuration from file"""
        ext = Path(filepath).suffix.lower()
        
        with open(filepath, 'r') as f:
            if ext in ['.yaml', '.yml']:
                return yaml.safe_load(f) or {}
            elif ext == '.json':
                return json.load(f)
            else:
                raise ValueError(f"Unsupported config format: {ext}")
    
    def _merge_configs(self, base: MFTConfig, override: Dict[str, Any]) -> MFTConfig:
        """Merge configuration dictionaries"""
        # This is a simplified merge - in production, use deep merge
        base_dict = base.to_dict()
        
        for key, value in override.items():
            if key in base_dict:
                if isinstance(value, dict) and isinstance(base_dict[key], dict):
                    base_dict[key].update(value)
                else:
                    base_dict[key] = value
        
        return self._dict_to_config(base_dict)
    
    def _override_from_env(self, config: MFTConfig) -> MFTConfig:
        """Override configuration from environment variables"""
        config_dict = config.to_dict()
        
        # App settings
        if os.getenv('MFT_ENVIRONMENT'):
            config_dict['app']['environment'] = os.getenv('MFT_ENVIRONMENT')
        if os.getenv('MFT_DEBUG'):
            config_dict['app']['debug'] = os.getenv('MFT_DEBUG').lower() == 'true'
        if os.getenv('MFT_LOG_LEVEL'):
            config_dict['app']['log_level'] = os.getenv('MFT_LOG_LEVEL')
        
        # API settings
        if os.getenv('MFT_API_HOST'):
            config_dict['api']['host'] = os.getenv('MFT_API_HOST')
        if os.getenv('MFT_API_PORT'):
            config_dict['api']['port'] = int(os.getenv('MFT_API_PORT'))
        
        # Database settings
        if os.getenv('MFT_DB_HOST'):
            config_dict['database']['host'] = os.getenv('MFT_DB_HOST')
        if os.getenv('MFT_DB_PORT'):
            config_dict['database']['port'] = int(os.getenv('MFT_DB_PORT'))
        if os.getenv('MFT_DB_NAME'):
            config_dict['database']['database'] = os.getenv('MFT_DB_NAME')
        if os.getenv('MFT_DB_USER'):
            config_dict['database']['username'] = os.getenv('MFT_DB_USER')
        if os.getenv('MFT_DB_PASSWORD'):
            config_dict['database']['password'] = os.getenv('MFT_DB_PASSWORD')
        
        # Redis settings
        if os.getenv('MFT_REDIS_HOST'):
            config_dict['redis']['host'] = os.getenv('MFT_REDIS_HOST')
        if os.getenv('MFT_REDIS_PORT'):
            config_dict['redis']['port'] = int(os.getenv('MFT_REDIS_PORT'))
        if os.getenv('MFT_REDIS_PASSWORD'):
            config_dict['redis']['password'] = os.getenv('MFT_REDIS_PASSWORD')
        
        # Security settings
        if os.getenv('MFT_SECRET_KEY'):
            config_dict['security']['secret_key'] = os.getenv('MFT_SECRET_KEY')
        
        # Email settings
        if os.getenv('MFT_EMAIL_SMTP_HOST'):
            config_dict['alerts']['email']['smtp_host'] = os.getenv('MFT_EMAIL_SMTP_HOST')
        if os.getenv('MFT_EMAIL_USERNAME'):
            config_dict['alerts']['email']['username'] = os.getenv('MFT_EMAIL_USERNAME')
        if os.getenv('MFT_EMAIL_PASSWORD'):
            config_dict['alerts']['email']['password'] = os.getenv('MFT_EMAIL_PASSWORD')
        
        # Slack settings
        if os.getenv('MFT_SLACK_WEBHOOK_URL'):
            config_dict['alerts']['slack']['webhook_url'] = os.getenv('MFT_SLACK_WEBHOOK_URL')
        
        return self._dict_to_config(config_dict)
    
    def _dict_to_config(self, config_dict: Dict[str, Any]) -> MFTConfig:
        """Convert dictionary to MFTConfig"""
        return MFTConfig(
            app=AppConfig(**config_dict.get('app', {})),
            api=APIConfig(**config_dict.get('api', {})),
            monitoring=MonitoringConfig(**config_dict.get('monitoring', {})),
            alerts=AlertConfig(
                enabled=config_dict.get('alerts', {}).get('enabled', True),
                email=EmailConfig(**config_dict.get('alerts', {}).get('email', {})),
                slack=SlackConfig(**config_dict.get('alerts', {}).get('slack', {})),
                webhook=WebhookConfig(**config_dict.get('alerts', {}).get('webhook', {}))
            ),
            compliance=ComplianceConfig(**config_dict.get('compliance', {})),
            scheduler=SchedulerConfig(**config_dict.get('scheduler', {})),
            database=DatabaseConfig(**config_dict.get('database', {})),
            redis=RedisConfig(**config_dict.get('redis', {})),
            storage=StorageConfig(**config_dict.get('storage', {})),
            security=SecurityConfig(**config_dict.get('security', {}))
        )
    
    def _validate_config(self, config: MFTConfig):
        """Validate configuration"""
        errors = []
        
        # Validate API port
        if not 1 <= config.api.port <= 65535:
            errors.append(f"Invalid API port: {config.api.port}")
        
        # Validate log level
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if config.app.log_level not in valid_log_levels:
            errors.append(f"Invalid log level: {config.app.log_level}")
        
        # Validate secret key in production
        if config.app.environment == 'production':
            if not config.security.secret_key:
                errors.append("Secret key is required in production")
            
            if config.app.debug:
                logger.warning("Debug mode enabled in production - this is not recommended")
        
        # Validate email config if enabled
        if config.alerts.email.enabled:
            if not config.alerts.email.from_address:
                errors.append("Email from_address is required when email alerts are enabled")
            if not config.alerts.email.to_addresses:
                errors.append("Email to_addresses is required when email alerts are enabled")
        
        # Validate Slack config if enabled
        if config.alerts.slack.enabled:
            if not config.alerts.slack.webhook_url:
                errors.append("Slack webhook_url is required when Slack alerts are enabled")
        
        # Validate database config if enabled
        if config.database.enabled:
            if not config.database.password and config.app.environment == 'production':
                logger.warning("Database password not set in production")
        
        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(errors)
            raise ValueError(error_msg)
        
        logger.info("Configuration validation passed")
    
    def get(self) -> MFTConfig:
        """Get current configuration"""
        if not self.config:
            return self.load()
        return self.config
    
    def reload(self) -> MFTConfig:
        """Reload configuration"""
        logger.info("Reloading configuration...")
        return self.load()


# Global configuration instance
_config_manager: Optional[ConfigurationManager] = None


def get_config_manager(config_path: Optional[str] = None) -> ConfigurationManager:
    """Get global configuration manager instance"""
    global _config_manager
    
    if _config_manager is None:
        _config_manager = ConfigurationManager(config_path)
    
    return _config_manager


def get_config(config_path: Optional[str] = None) -> MFTConfig:
    """Get application configuration"""
    manager = get_config_manager(config_path)
    return manager.get()


# Example usage
if __name__ == "__main__":
    # Create default configuration
    config = MFTConfig()
    config.save('config.example.yaml')
    
    # Load configuration
    manager = ConfigurationManager('config.example.yaml')
    loaded_config = manager.load()
    
    print(f"Environment: {loaded_config.app.environment}")
    print(f"API Port: {loaded_config.api.port}")
    print(f"Monitoring Enabled: {loaded_config.monitoring.enabled}")
