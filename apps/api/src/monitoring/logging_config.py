"""
Structured Logging Configuration for NFMD API
Provides centralized, structured logging with proper formatting, filtering, and output handlers.
"""

import json
import logging
import logging.config
import os
import sys
from datetime import datetime
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {"name", "msg", "args", "levelname", "levelno", "pathname",
                          "filename", "module", "lineno", "funcName", "created",
                          "msecs", "relativeCreated", "thread", "threadName",
                          "processName", "process", "exc_info", "exc_text", "stack_info"}:
                log_data[key] = value

        return json.dumps(log_data)


class SensitiveDataFilter(logging.Filter):
    """Filter to remove sensitive data from logs."""

    SENSITIVE_PATTERNS = [
        "password",
        "token",
        "api_key",
        "secret",
        "credential",
        "ssh_key",
        "authorization",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter sensitive data from log message."""
        msg = record.getMessage().lower()

        # Check if message contains sensitive patterns
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in msg:
                # Replace with redacted message
                record.msg = f"[REDACTED: {pattern.upper()}]"
                record.args = ()

        return True


def setup_logging(
    log_level: str = "INFO",
    log_file: str | None = None,
    environment: str = "production"
) -> None:
    """
    Setup structured logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for file logging
        environment: Environment name for context
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Default log file if not specified
    if not log_file:
        log_file = log_dir / f"nfmd-{environment}.log"

    # Ensure log file directory exists
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": "apps.api.src.monitoring.logging_config.StructuredFormatter",
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]",
            },
        },
        "filters": {
            "sensitive": {
                "()": "apps.api.src.monitoring.logging_config.SensitiveDataFilter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "structured",
                "filters": ["sensitive"],
                "stream": sys.stdout,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": log_level,
                "formatter": "structured",
                "filters": ["sensitive"],
                "filename": str(log_file),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "structured",
                "filters": ["sensitive"],
                "filename": str(log_dir / f"nfmd-{environment}-error.log"),
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
            },
        },
        "loggers": {
            "": {  # Root logger
                "level": log_level,
                "handlers": ["console", "file", "error_file"],
                "propagate": False,
            },
            "uvicorn": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
            },
            "sqlalchemy": {
                "level": "WARNING",
                "handlers": ["console"],
                "propagate": False,
            },
        },
    }

    # Apply logging configuration
    logging.config.dictConfig(logging_config)


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding contextual information to logs."""

    def __init__(self, **context):
        """
        Initialize log context.

        Args:
            **context: Key-value pairs to add to log context
        """
        self.context = context
        self.logger = logging.getLogger(__name__)
        self.old_factory = None

    def __enter__(self):
        """Add context to current logger."""
        self.old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.context.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Remove context from logger."""
        logging.setLogRecordFactory(self.old_factory)


def log_request(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    user_id: str | None = None,
    **kwargs
) -> None:
    """
    Log HTTP request with structured data.

    Args:
        method: HTTP method
        path: Request path
        status_code: Response status code
        duration_ms: Request duration in milliseconds
        user_id: Optional user ID
        **kwargs: Additional context
    """
    logger = get_logger("http.request")

    log_data = {
        "http_method": method,
        "request_path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "user_id": user_id,
        **kwargs
    }

    # Determine log level based on status code
    if status_code >= 500:
        logger.error("HTTP request", extra=log_data)
    elif status_code >= 400:
        logger.warning("HTTP request", extra=log_data)
    else:
        logger.info("HTTP request", extra=log_data)


def log_task_event(
    task_id: str,
    event_type: str,
    status: str,
    duration_seconds: float | None = None,
    **kwargs
) -> None:
    """
    Log MD task event with structured data.

    Args:
        task_id: Task ID
        event_type: Event type (created, started, completed, failed)
        status: Task status
        duration_seconds: Optional task duration
        **kwargs: Additional context
    """
    logger = get_logger("md.task")

    log_data = {
        "task_id": task_id,
        "event_type": event_type,
        "status": status,
        "duration_seconds": duration_seconds,
        **kwargs
    }

    # Determine log level based on status
    if status in ["failed", "error"]:
        logger.error("MD task event", extra=log_data)
    elif status in ["completed", "success"]:
        logger.info("MD task event", extra=log_data)
    else:
        logger.debug("MD task event", extra=log_data)


def log_hpc_event(
    cluster: str,
    event_type: str,
    success: bool,
    duration_seconds: float | None = None,
    **kwargs
) -> None:
    """
    Log HPC cluster event with structured data.

    Args:
        cluster: HPC cluster name
        event_type: Event type (job_submit, job_check, connection_test)
        success: Whether the operation succeeded
        duration_seconds: Optional operation duration
        **kwargs: Additional context
    """
    logger = get_logger("hpc.cluster")

    log_data = {
        "cluster": cluster,
        "event_type": event_type,
        "success": success,
        "duration_seconds": duration_seconds,
        **kwargs
    }

    # Determine log level based on success
    if not success:
        logger.error("HPC cluster event", extra=log_data)
    else:
        logger.info("HPC cluster event", extra=log_data)


def log_database_query(
    query_type: str,
    table: str,
    duration_ms: float,
    rows_affected: int | None = None,
    **kwargs
) -> None:
    """
    Log database query with structured data.

    Args:
        query_type: Type of query (SELECT, INSERT, UPDATE, DELETE)
        table: Table name
        duration_ms: Query duration in milliseconds
        rows_affected: Optional number of rows affected
        **kwargs: Additional context
    """
    logger = get_logger("database.query")

    log_data = {
        "query_type": query_type,
        "table": table,
        "duration_ms": duration_ms,
        "rows_affected": rows_affected,
        **kwargs
    }

    # Warn if query is slow (> 1 second)
    if duration_ms > 1000:
        logger.warning("Database query (SLOW)", extra=log_data)
    else:
        logger.debug("Database query", extra=log_data)


# Environment-based logging setup
def initialize_logging():
    """Initialize logging based on environment variable."""
    environment = os.getenv("ENVIRONMENT", "development")
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    if environment == "production":
        setup_logging(
            log_level=log_level,
            environment=environment
        )
    else:
        # Development logging - more verbose
        setup_logging(
            log_level="DEBUG",
            environment=environment
        )


# Auto-initialize on import
initialize_logging()
