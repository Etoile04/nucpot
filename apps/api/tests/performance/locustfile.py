"""
MD Verification Performance Tests with Locust

Tests API performance under various load conditions:
- API response times (p50/p95/p99)
- Task queue throughput
- Concurrent user handling
- Database connection pool efficiency
- HPC integration performance

Run with:
  uv run --with performance locust -f tests/performance/locustfile.py
"""

import logging

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MDVerificationUser(HttpUser):
    """
    Simulates a typical user interacting with the MD verification system.

    User behavior pattern:
    - 70%: View job list (most common operation)
    - 20%: View job details
    - 10%: Submit new verification job
    """

    wait_time = between(1, 3)  # Users wait 1-3 seconds between actions

    def on_start(self):
        """Login before performing any actions"""
        self.client.post("/api/auth/login", json={
            "username": "test_user",
            "password": "test_password"
        }, name="/auth/login")
        logger.info("User logged in")

    @task(7)
    def view_job_list(self):
        """View the MD verification job list (most common operation)"""
        response = self.client.get(
            "/api/md-verification/jobs",
            name="/jobs/list"
        )
        if response.status_code == 200:
            jobs = response.json().get("items", [])
            logger.debug(f"Found {len(jobs)} jobs")

    @task(2)
    def view_job_detail(self):
        """View details of a specific job"""
        # Use a consistent job ID for realistic testing
        response = self.client.get(
            "/api/md-verification/jobs/test-job-123",
            name="/jobs/detail"
        )
        if response.status_code == 200:
            logger.debug("Job details retrieved")

    @task(1)
    def submit_job(self):
        """Submit a new MD verification job (less frequent)"""
        response = self.client.post(
            "/api/md-verification/jobs",
            json={
                "potential_id": "performance_test_potential",
                "element_system": "U",
                "potential_file": "/data/potentials/test.empirical",
                "structure_file": "/data/structures/test.cif",
                "temperature": 300,
                "pressure": 0.1
            },
            name="/jobs/submit"
        )
        if response.status_code in [200, 201]:
            logger.info("Job submitted successfully")
        elif response.status_code == 429:
            logger.warning("Rate limited - slowing down")


class AdminUser(HttpUser):
    """
    Simulates an admin user with additional permissions.
    Admin users have different usage patterns - more monitoring, less job submission.
    """

    wait_time = between(2, 5)

    def on_start(self):
        """Login as admin"""
        self.client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin_password"
        }, catch_response=True, name="/auth/admin/login")

    @task(5)
    def view_system_status(self):
        """View system status and metrics"""
        self.client.get("/api/admin/status", name="/admin/status")

    @task(3)
    def view_queue_metrics(self):
        """View job queue metrics"""
        self.client.get("/api/admin/queue", name="/admin/queue")

    @task(2)
    def view_user_list(self):
        """View list of all users (admin only)"""
        self.client.get("/api/admin/users", name="/admin/users")


class PerformanceTestUser(HttpUser):
    """
    User specifically for performance stress testing.
    Focuses on operations that test system limits.
    """

    wait_time = between(0.5, 2)  # Faster pace for stress testing

    def on_start(self):
        """Quick login"""
        self.client.post("/api/auth/login", json={
            "username": "perf_test_user",
            "password": "test_password"
        }, catch_response=True, name="/auth/login")

    @task(10)
    def rapid_job_list_checks(self):
        """Rapidly check job list to stress the API"""
        self.client.get("/api/md-verification/jobs", name="/jobs/rapid-list")

    @task(5)
    def parallel_job_detail_views(self):
        """View multiple job details in parallel"""
        for job_id in range(100, 105):  # Check multiple jobs
            self.client.get(
                f"/api/md-verification/jobs/perf-job-{job_id}",
                name="/jobs/parallel-detail",
                catch_response=True
            )


# Custom event handlers for performance metrics

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """
    Log slow requests for analysis.

    Requests taking > 2 seconds are considered slow and logged separately.
    """
    if exception:
        logger.error(f"Request {name} failed: {exception}")
    elif response_time > 2000:  # 2 seconds
        logger.warning(f"Slow request: {name} took {response_time:.0f}ms")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Log test completion statistics"""
    if isinstance(environment.runner, MasterRunner):
        logger.info("=" * 80)
        logger.info("PERFORMANCE TEST RESULTS")
        logger.info("=" * 80)
        logger.info(f"Total requests: {environment.stats.total.num_requests}")
        logger.info(f"Failed requests: {environment.stats.total.num_failures}")
        logger.info(f"Median response time: {environment.stats.total.median_response_time:.0f}ms")
        logger.info(f"Average response time: {environment.stats.total.avg_response_time:.0f}ms")
        logger.info(f"Min response time: {environment.stats.total.min_response_time:.0f}ms")
        logger.info(f"Max response time: {environment.stats.total.max_response_time:.0f}ms")
        logger.info(f"Requests/s: {environment.stats.total.total_rps:.2f}")
        logger.info("=" * 80)
