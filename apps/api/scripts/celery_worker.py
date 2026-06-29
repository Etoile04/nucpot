"""Celery worker startup script for MD verification tasks.

Usage:
    # Development (single worker)
    python scripts/celery_worker.py

    # Production with scaling
    celery -A nfm_db.celery_app worker -Q md_verification -c 4

    # With flower monitoring
    celery -A nfm_db.celery_app flower --port=5555
"""

from __future__ import annotations

import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nfm_db.services.celery_app import celery_app


def main() -> None:
    """Launch Celery worker for MD verification tasks."""
    import click

    @click.command()
    @click.option(
        "--queue",
        default="md_verification",
        help="Celery queue to consume from",
    )
    @click.option(
        "--concurrency",
        default=1,
        type=int,
        help="Number of worker processes (default: 1 for long-running tasks)",
    )
    @click.option(
        "--loglevel",
        default="INFO",
        type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
        help="Log level",
    )
    @click.option(
        "--max-tasks",
        default=1,
        type=int,
        help="Max tasks per child before restart (default: 1 for memory cleanup)",
    )
    def start_worker(
        queue: str,
        concurrency: int,
        loglevel: str,
        max_tasks: int,
    ) -> None:
        """Start Celery worker for MD verification tasks.

        Args:
            queue: Celery queue to consume from
            concurrency: Number of worker processes
            loglevel: Logging level
            max_tasks: Max tasks per child before restart
        """
        from celery.bin.worker import worker

        # Create worker instance
        w = worker.worker(
            app=celery_app,
            queue=queue,
            concurrency=concurrency,
            loglevel=loglevel,
            max_tasks_per_child=max_tasks,
        )

        # Start worker
        w.start()

    start_worker()


if __name__ == "__main__":
    main()
